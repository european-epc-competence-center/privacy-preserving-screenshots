"""Linux: talking to the desktop through xdg-desktop-portal, over jeepney.

Wayland forbids reading the screen and grabbing keys, so the desktop does these
on eecc-redact's behalf and asks the user where it has to; starting at login goes
the same way. jeepney rather than QtDBus: portal messages
carry nested types such as a(sa{sv}) that PySide cannot marshal.

Requests block until the desktop answers, which can mean waiting for the user.
Make them off the Qt main thread (see eecc_redact.ui.run_blocking).
"""

import uuid
from collections.abc import Callable
from contextlib import suppress
from queue import Empty
from typing import Any

from eecc_redact import APP_ID, MODULE
from eecc_redact.errors import AppError, Cancelled

SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
REQUEST = "org.freedesktop.portal.Request"
REGISTRY = "org.freedesktop.host.portal.Registry"
#: What D-Bus answers when a desktop lacks a portal.
MISSING = frozenset(
    {
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.ServiceUnknown",
    }
)


class PortalError(AppError):
    def __init__(self, message: str, *, name: str = "") -> None:
        super().__init__(message)
        #: The D-Bus error name, when the desktop answered with one.
        self.name = name


class _Deliver:
    """Stands in for a jeepney queue: hands each message to a callback instead."""

    def __init__(self, callback: Callable[[Any], None]) -> None:
        self.callback = callback

    def put_nowait(self, message: Any) -> None:
        self.callback(message)


class Portal:
    """One session-bus connection to the portal."""

    def __init__(self) -> None:
        from jeepney import message_bus
        from jeepney.io.threading import DBusRouter, Proxy, open_dbus_connection

        try:
            self._connection = open_dbus_connection(bus="SESSION")
        except Exception as exc:  # no bus address, socket refused, auth failed
            raise PortalError(f"No D-Bus session bus ({exc}).") from exc
        self._router = DBusRouter(self._connection)
        self._bus = Proxy(message_bus, self._router, timeout=10)
        self._filters: list = []
        # The portal cannot tell which app this is. Say so, or GlobalShortcuts
        # refuses with "An app id is required". The desktop accepts the ID only
        # if a matching .desktop entry is installed.
        with suppress(PortalError):
            self.call(REGISTRY, "Register", "sa{sv}", (APP_ID, {}))

    def __enter__(self) -> "Portal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, interface: str, method: str, signature: str, body: tuple) -> tuple:
        from jeepney import DBusAddress, HeaderFields, MessageType, new_method_call

        message = new_method_call(DBusAddress(PATH, SERVICE, interface), method, signature, body)
        try:
            reply = self._router.send_and_get_reply(message, timeout=10)
        except TimeoutError as exc:
            raise PortalError(f"The desktop did not answer {method}.") from exc
        if reply.header.message_type == MessageType.error:
            raise PortalError(
                str(reply.body[0]) if reply.body else f"{method} failed.",
                name=reply.header.fields.get(HeaderFields.error_name, ""),
            )
        return reply.body

    def request(
        self,
        interface: str,
        method: str,
        signature: str,
        body: tuple,
        options: dict[str, tuple[str, Any]],
        *,
        timeout: float | None = 60,
    ) -> dict:
        """Call a method that answers later through a Request object, and wait.

        `options` is the method's trailing a{sv}, as (signature, value) pairs.
        Returns the results with their top-level variants unwrapped.
        """
        from jeepney import MatchRule

        token = f"{MODULE}_{uuid.uuid4().hex[:12]}"
        sender = self._router.unique_name.lstrip(":").replace(".", "_")
        rule = MatchRule(
            type="signal",
            interface=REQUEST,
            member="Response",
            path=f"{PATH}/request/{sender}/{token}",
        )
        self._bus.AddMatch(rule)  # before calling: the desktop may answer at once
        try:
            with self._router.filter(rule) as responses:
                self.call(
                    interface, method, signature, (*body, {**options, "handle_token": ("s", token)})
                )
                try:
                    code, results = responses.get(timeout=timeout).body
                except Empty as exc:
                    raise PortalError(f"The desktop did not finish {method}.") from exc
        finally:
            self._bus.RemoveMatch(rule)
        if code != 0:
            # 1 is "cancelled"; 2 is "the interaction was ended in some other
            # way", which is also what GNOME answers when its screenshot picker
            # is closed with Esc. Either way the user backed out: no result, no error.
            raise Cancelled()
        return {key: value for key, (_signature, value) in results.items()}

    def subscribe(self, interface: str, member: str, callback: Callable[[tuple], None]) -> None:
        """Run `callback(body)` for every such signal, on jeepney's receiver thread."""
        from jeepney import MatchRule

        rule = MatchRule(type="signal", interface=interface, member=member, path=PATH)
        self._bus.AddMatch(rule)
        self._filters.append(
            self._router.filter(rule, queue=_Deliver(lambda message: callback(message.body)))
        )

    def version(self, interface: str) -> int | None:
        """The portal interface's version, or None where the desktop lacks it."""
        try:
            (value,) = self.call(
                "org.freedesktop.DBus.Properties", "Get", "ss", (interface, "version")
            )
        except PortalError:
            return None
        return int(value[1])  # a variant: (signature, value)

    def close(self) -> None:
        """Hang up. The desktop then ends every session eecc-redact held, shortcuts included."""
        for handle in self._filters:
            handle.close()
        self._filters.clear()
        self._router.close()
        self._connection.close()
