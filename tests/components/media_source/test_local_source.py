"""Test Local Media Source."""
from http import HTTPStatus
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from homeassistant.components import media_source
from homeassistant.components.media_source import const
from homeassistant.config import async_process_ha_core_config
from homeassistant.setup import async_setup_component


@pytest.fixture
async def temp_dir(hass):
    """Return a temp dir."""
    with TemporaryDirectory() as tmpdirname:
        target_dir = Path(tmpdirname) / "another_subdir"
        target_dir.mkdir()
        await async_process_ha_core_config(
            hass, {"media_dirs": {"test_dir": str(target_dir)}}
        )
        assert await async_setup_component(hass, const.DOMAIN, {})

        yield str(target_dir)


async def test_async_browse_media(hass):
    """Test browse media."""
    local_media = hass.config.path("media")
    await async_process_ha_core_config(
        hass, {"media_dirs": {"local": local_media, "recordings": local_media}}
    )
    await hass.async_block_till_done()

    assert await async_setup_component(hass, const.DOMAIN, {})
    await hass.async_block_till_done()

    # Test path not exists
    with pytest.raises(media_source.BrowseError) as excinfo:
        await media_source.async_browse_media(
            hass, f"{const.URI_SCHEME}{const.DOMAIN}/local/test/not/exist"
        )
    assert str(excinfo.value) == "Path does not exist."

    # Test browse file
    with pytest.raises(media_source.BrowseError) as excinfo:
        await media_source.async_browse_media(
            hass, f"{const.URI_SCHEME}{const.DOMAIN}/local/test.mp3"
        )
    assert str(excinfo.value) == "Path is not a directory."

    # Test invalid base
    with pytest.raises(media_source.BrowseError) as excinfo:
        await media_source.async_browse_media(
            hass, f"{const.URI_SCHEME}{const.DOMAIN}/invalid/base"
        )
    assert str(excinfo.value) == "Unknown source directory."

    # Test directory traversal
    with pytest.raises(media_source.BrowseError) as excinfo:
        await media_source.async_browse_media(
            hass, f"{const.URI_SCHEME}{const.DOMAIN}/local/../configuration.yaml"
        )
    assert str(excinfo.value) == "Invalid path."

    # Test successful listing
    media = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{const.DOMAIN}"
    )
    assert media

    media = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{const.DOMAIN}/local/."
    )
    assert media

    media = await media_source.async_browse_media(
        hass, f"{const.URI_SCHEME}{const.DOMAIN}/recordings/."
    )
    assert media


async def test_media_view(hass, hass_client):
    """Test media view."""
    local_media = hass.config.path("media")
    await async_process_ha_core_config(
        hass, {"media_dirs": {"local": local_media, "recordings": local_media}}
    )
    await hass.async_block_till_done()

    assert await async_setup_component(hass, const.DOMAIN, {})
    await hass.async_block_till_done()

    client = await hass_client()

    # Protects against non-existent files
    resp = await client.get("/media/local/invalid.txt")
    assert resp.status == HTTPStatus.NOT_FOUND

    resp = await client.get("/media/recordings/invalid.txt")
    assert resp.status == HTTPStatus.NOT_FOUND

    # Protects against non-media files
    resp = await client.get("/media/local/not_media.txt")
    assert resp.status == HTTPStatus.NOT_FOUND

    # Protects against unknown local media sources
    resp = await client.get("/media/unknown_source/not_media.txt")
    assert resp.status == HTTPStatus.NOT_FOUND

    # Fetch available media
    resp = await client.get("/media/local/test.mp3")
    assert resp.status == HTTPStatus.OK

    resp = await client.get("/media/local/Epic Sax Guy 10 Hours.mp4")
    assert resp.status == HTTPStatus.OK

    resp = await client.get("/media/recordings/test.mp3")
    assert resp.status == HTTPStatus.OK


async def test_upload_view(hass, hass_client, temp_dir, hass_admin_user):
    """Allow uploading media."""

    img = (Path(__file__).parent.parent / "image/logo.png").read_bytes()

    def get_file(name):
        pic = io.BytesIO(img)
        pic.name = name
        return pic

    client = await hass_client()

    # Test normal upload
    res = await client.post(
        "/api/media_source/local_source/upload",
        data={
            "media_content_id": "media-source://media_source/test_dir/.",
            "file": get_file("logo.png"),
        },
    )

    assert res.status == 200
    assert (Path(temp_dir) / "logo.png").is_file()

    # Test with bad media source ID
    for bad_id in (
        # Subdir doesn't exist
        "media-source://media_source/test_dir/some-other-dir",
        # Main dir doesn't exist
        "media-source://media_source/test_dir2",
        # Location is invalid
        "media-source://media_source/test_dir/..",
        # Domain != media_source
        "media-source://nest/test_dir/.",
        # Completely something else
        "http://bla",
    ):
        res = await client.post(
            "/api/media_source/local_source/upload",
            data={
                "media_content_id": bad_id,
                "file": get_file("bad-source-id.png"),
            },
        )

        assert res.status == 400
        assert not (Path(temp_dir) / "bad-source-id.png").is_file()

    # Test invalid POST data
    res = await client.post(
        "/api/media_source/local_source/upload",
        data={
            "media_content_id": "media-source://media_source/test_dir/.",
            "file": get_file("invalid-data.png"),
            "incorrect": "format",
        },
    )

    assert res.status == 400
    assert not (Path(temp_dir) / "invalid-data.png").is_file()

    # Test invalid content type
    text_file = io.BytesIO(b"Hello world")
    text_file.name = "hello.txt"
    res = await client.post(
        "/api/media_source/local_source/upload",
        data={
            "media_content_id": "media-source://media_source/test_dir/.",
            "file": text_file,
        },
    )

    assert res.status == 400
    assert not (Path(temp_dir) / "hello.txt").is_file()

    # Test invalid filename
    with patch(
        "aiohttp.formdata.guess_filename", return_value="../invalid-filename.png"
    ):
        res = await client.post(
            "/api/media_source/local_source/upload",
            data={
                "media_content_id": "media-source://media_source/test_dir/.",
                "file": get_file("../invalid-filename.png"),
            },
        )

    assert res.status == 400
    assert not (Path(temp_dir) / "../invalid-filename.png").is_file()

    # Remove admin access
    hass_admin_user.groups = []
    res = await client.post(
        "/api/media_source/local_source/upload",
        data={
            "media_content_id": "media-source://media_source/test_dir/.",
            "file": get_file("no-admin-test.png"),
        },
    )

    assert res.status == 401
    assert not (Path(temp_dir) / "no-admin-test.png").is_file()
