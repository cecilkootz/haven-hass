"""Config flow for Haven Lighting integration."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from havenlighting import HavenClient
from havenlighting.exceptions import ApiError, AuthenticationError

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class HavenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Haven Lighting."""

    VERSION = 1

    async def _async_try_login(self, email: str, password: str) -> str | None:
        """Log in with the given credentials; return an error key or None."""
        client = HavenClient()
        try:
            authenticated = await self.hass.async_add_executor_job(
                client.authenticate, email, password
            )
        except AuthenticationError:
            return "invalid_auth"
        except ApiError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected exception")
            return "unknown"
        return None if authenticated else "invalid_auth"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL])
            self._abort_if_unique_id_configured()
            error = await self._async_try_login(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error is None:
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL], data=user_input
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else None,
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication upon a Haven Lighting authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication dialog.

        Only the password is asked for: the email is the entry's unique id,
        and switching accounts belongs in a new entry, not a reauth.
        """
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        email = reauth_entry.data[CONF_EMAIL]

        if user_input is not None:
            error = await self._async_try_login(email, user_input[CONF_PASSWORD])
            if error is None:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )
