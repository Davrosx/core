"""Utility methods for initializing a Jellyfin client."""
from __future__ import annotations

import logging
import re
import socket
from typing import Any

from jellyfin_apiclient_python import Jellyfin, JellyfinClient
from jellyfin_apiclient_python.api import API
from jellyfin_apiclient_python.connection_manager import (
    CONNECTION_STATE,
    ConnectionManager,
)

from homeassistant import exceptions
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from urllib.parse import urlparse, urlunparse
from .const import CLIENT_VERSION, ITEM_KEY_IMAGE_TAGS, USER_AGENT, USER_APP_NAME

# Get logger for this module
_LOGGER = logging.getLogger(__name__)

async def validate_input(
    hass: HomeAssistant, user_input: dict[str, Any], client: JellyfinClient
) -> tuple[str, dict[str, Any]]:
    """Validate that the provided url and credentials can be used to connect."""
    url = user_input[CONF_URL]
    username = user_input[CONF_USERNAME]
    password = user_input[CONF_PASSWORD]

    user_id, connect_result = await hass.async_add_executor_job(
        _connect, client, url, username, password
    )

    return (user_id, connect_result)


def create_client(device_id: str, device_name: str | None = None) -> JellyfinClient:
    """Create a new Jellyfin client."""
    if device_name is None:
        device_name = socket.gethostname()

    jellyfin = Jellyfin()

    client = jellyfin.get_client()
    client.config.app(USER_APP_NAME, CLIENT_VERSION, device_name, device_id)
    client.config.http(USER_AGENT)

    return client


def _connect(
    client: JellyfinClient, url: str, username: str, password: str
) -> tuple[str, dict[str, Any]]:
    """Connect to the Jellyfin server and assert that the user can login."""
    client.config.data["auth.ssl"] = url.startswith("https")

    connect_result = _connect_to_address(client.auth, url)

    _login(client.auth, url, username, password)

    return (_get_user_id(client.jellyfin), connect_result)


def _connect_to_address(
    connection_manager: ConnectionManager, url: str
) -> dict[str, Any]:
    """Connect to the Jellyfin server."""
    result: dict[str, Any] = connection_manager.connect_to_address(url)
    if CONNECTION_STATE(result["State"]) != CONNECTION_STATE.ServerSignIn:
        raise CannotConnect

    return result


def _login(
    connection_manager: ConnectionManager,
    url: str,
    username: str,
    password: str,
) -> None:
    """Assert that the user can log in to the Jellyfin server."""
    response = connection_manager.login(url, username, password)

    if "AccessToken" not in response:
        raise InvalidAuth


def _get_user_id(api: API) -> str:
    """Set the unique userid from a Jellyfin server."""
    settings: dict[str, Any] = api.get_user_settings()
    userid: str = settings["Id"]
    return userid


def _normalize_url_path_keep_query(url: str) -> str:
    """Normalize repeated slashes in the path component of a URL while preserving query."""
    parsed = urlparse(url)
    # Collapse multiple slashes in the path to a single slash (preserve leading slash)
    normalized_path = re.sub(r"/+", "/", parsed.path)
    if normalized_path != parsed.path:
        new = parsed._replace(path=normalized_path)
        return urlunparse(new)
    return url


def get_artwork_url(
    client: JellyfinClient, item: dict[str, Any], max_width: int = 600
) -> str | None:
    """Find a suitable thumbnail for an item."""
    artwork_id: str | None = None
    artwork_type: str | None = None
    parent_backdrop_id: str | None = item.get("ParentBackdropItemId")

    # Be defensive: ITEM_KEY_IMAGE_TAGS may not be present
    image_tags = item.get(ITEM_KEY_IMAGE_TAGS) or []

    if "AlbumPrimaryImageTag" in item:
        # jellyfin_apiclient_python doesn't support passing a specific tag to `.artwork`,
        # so we don't use the actual value of AlbumPrimaryImageTag.
        # However, its mere presence tells us that the album does have primary artwork,
        # and the resulting URL will pull the primary album art even if the tag is not specified.
        artwork_type = "Primary"
        artwork_id = item["AlbumId"]
    elif "Backdrop" in image_tags:
        artwork_type = "Backdrop"
        artwork_id = item["Id"]
    elif parent_backdrop_id:
        artwork_type = "Backdrop"
        artwork_id = parent_backdrop_id
    elif "Primary" in image_tags:
        artwork_type = "Primary"
        artwork_id = item["Id"]
    else:
        return None

    artwork_url = client.jellyfin.artwork(artwork_id, artwork_type, max_width)

    # DEBUG LOGGING - This will appear in HA logs
    _LOGGER.debug(
        "Artwork URL debug - Original: %s, Type: %s, ID: %s, ArtType: %s, Width: %s",
        artwork_url,
        type(artwork_url),
        artwork_id,
        artwork_type,
        max_width,
    )

    if artwork_url:
        artwork_url_str = str(artwork_url)

        # Normalize only the path (collapse duplicate slashes) while preserving query parameters.
        fixed_url = _normalize_url_path_keep_query(artwork_url_str)
        if fixed_url != artwork_url_str:
            _LOGGER.debug("Normalized artwork URL path: %s -> %s", artwork_url_str, fixed_url)

        # Check if query params exist
        if "?" not in fixed_url:
            _LOGGER.warning("Artwork URL missing query parameters! Raw: %s", fixed_url)

        return fixed_url
    return None


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate the server is unreachable."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate the credentials are invalid."""
