import urllib.parse
from .common import exit_immediately
from .populate import get_streams
from .url import (
    sanitize_stream,
    sanitize,
)

from pathlib import Path
import json
import urllib
import zulip
import sys
import time
import requests
import os
import hashlib
from typing import (
    IO,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)


def get_index(json_root, index_name):
    index = json_root / Path(f"{index_name}_index.json")

    if not index.exists():
        error_msg = f"""
    You are trying to download avatars, but we cannot find
    a {index_name} index at {index}.

    Most likely, you have never built the index.  You can use the -t option
    of this script to build a full index one time.

    (It's also possible that you have built the index but modified the configuration
    or moved files in your file system.)
            """
        exit_immediately(error_msg)

    f = index.open("r", encoding="utf-8")
    index = json.load(f)
    f.close()
    return index


def get_emoji_assets(json_root, assets: set):
    emoji = get_index(json_root, "emoji")
    for e in emoji:
        assets.add(emoji[e]["source_url"])


def get_member_assets(json_root, assets: set):
    members = get_index(json_root, "member")
    for a in members.values():
        if a["avatar_url"] is None:
            hash = hashlib.md5(a["email"].encode("utf-8")).hexdigest()
            a["avatar_url"] = f"https://secure.gravatar.com/avatar/{hash}?d=identicon"
        assets.add(a["avatar_url"])


def get_message_assets(json_root, assets: set):
    stream_index = get_index(json_root, "stream")

    for name, stream in stream_index["streams"].items():
        print(name)

        topics = stream["topic_data"].keys()
        for topic_name in topics:
            p = (
                json_root
                / Path(sanitize_stream(name, stream["id"]))
                / Path(sanitize(topic_name) + ".json")
            )

            f = p.open("r", encoding="utf-8")
            topic_data = json.load(f)
            f.close()

            for msg in topic_data:
                for asset in msg["assets"]:
                    assets.add(asset)


def download_asset(client: zulip.Client, asset: str, zulip_url, assets_root: Path):
    asset_url = urllib.parse.urljoin(zulip_url, asset)
    asset_url_parsed = urllib.parse.urlparse(asset_url)
    if asset_url_parsed.netloc not in zulip_url:
        # TODO: Handle this case!
        print(f"Skipping {asset_url}")
        return None

    print(asset_url)

    file_location = assets_root / Path(asset_url_parsed.path.lstrip("/"))
    os.makedirs(file_location.parent.resolve(), exist_ok=True)

    if file_location.exists():
        print(f"Skipping {asset_url} because it already exists")
        return None

    response = download_zulip_file(client, asset_url)
    if response.status_code != 200:
        print(f"Error downloading {asset_url}: {response.status_code}")
        print(response.text)
        raise Exception(f"Error downloading {asset_url}: {response.status_code}")
        return None

    f = file_location.open("wb")
    f.write(response.content)
    f.close()


def download_all(client: zulip.Client, zulip_url, json_root: Path, assets_root: Path):
    assets = set()

    print("Scanning for assets...")
    get_emoji_assets(json_root, assets)
    get_member_assets(json_root, assets)
    get_message_assets(json_root, assets)

    print("Downloading assets...")

    if urllib.parse.urlparse(zulip_url).scheme == "":
        zulip_url = "https://" + zulip_url

    for a in assets:
        download_asset(client, a, zulip_url, assets_root)


def download_zulip_file(
    self: zulip.Client,
    url: str,
    timeout: Optional[float] = None,
) -> requests.Response:
    request_timeout = 15.0 if not timeout else timeout

    self.ensure_session()
    assert self.session is not None

    query_state = {
        "had_error_retry": False,
        "failures": 0,
    }  # type: Dict[str, Any]

    def error_retry(error_string: str) -> bool:
        if not self.retry_on_errors or query_state["failures"] >= 10:
            return False
        if self.verbose:
            if not query_state["had_error_retry"]:
                sys.stdout.write(
                    "zulip API(%s): connection error%s -- retrying."
                    % (
                        url.split(zulip.API_VERSTRING, 2)[0],
                        error_string,
                    )
                )
                query_state["had_error_retry"] = True
            else:
                sys.stdout.write(".")
            sys.stdout.flush()
        query_state["request"]["dont_block"] = json.dumps(True)
        time.sleep(1)
        query_state["failures"] += 1
        return True

    def end_error_retry(succeeded: bool) -> None:
        if query_state["had_error_retry"] and self.verbose:
            if succeeded:
                print("Success!")
            else:
                print("Failed!")

    while True:
        try:
            # Actually make the request!
            res = self.session.request(
                "GET",
                urllib.parse.urljoin(self.base_url, url),
                timeout=request_timeout,
            )

            self.has_connected = True

            # On 50x errors, try again after a short sleep
            if str(res.status_code).startswith("5"):
                if error_retry(f" (server {res.status_code})"):
                    continue
                # Otherwise fall through and process the python-requests error normally
        except (requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
            # Timeouts are either a Timeout or an SSLError; we
            # want the later exception handlers to deal with any
            # non-timeout other SSLErrors
            if (
                isinstance(e, requests.exceptions.SSLError)
                and str(e) != "The read operation timed out"
            ):
                raise zulip.UnrecoverableNetworkError("SSL Error")

            end_error_retry(False)
            raise
        except requests.exceptions.ConnectionError:
            if not self.has_connected:
                # If we have never successfully connected to the server, don't
                # go into retry logic, because the most likely scenario here is
                # that somebody just hasn't started their server, or they passed
                # in an invalid site.
                raise zulip.UnrecoverableNetworkError(
                    "cannot connect to server " + self.base_url
                )

            if error_retry(""):
                continue
            end_error_retry(False)
            raise
        except Exception:
            # We'll split this out into more cases as we encounter new bugs.
            raise

        if res.status_code == 200:
            end_error_retry(True)
            return res

        end_error_retry(False)
        return {
            "msg": "Unexpected error from the server",
            "result": "http-error",
            "status_code": res.status_code,
        }
