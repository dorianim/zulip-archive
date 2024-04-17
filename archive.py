#!/usr/bin/env python3

"""
This is the main program for the Zulip archive system.  For help:

    python archive.py -h

Note that this actual file mostly does the following:

    parse command line arguments
    check some settings from settings.py
    complain if you haven't made certain directories

The actual work is done in two main libraries:

    lib/html.py
    lib/populate.py
"""


# The workflow (timing for the leanprover Zulip chat, on my slow laptop):
# - populate_all() builds a json file in `settings.json_directory` for each topic,
#   containing message data and an index json file mapping streams to their topics.
#   This uses the Zulip API and takes ~10 minutes to crawl the whole chat.
# - populate_incremental() assumes there is already a json cache and collects only new messages.
# - build_website() builds the webstie
# - See hosting.md for suggestions on hosting.
#

import sys

if sys.version_info < (3, 6):
    version_error = " Python version must be 3.6 or higher\n\
            Your current version of python is {}.{}\n\
            Please try again with python3.".format(
        sys.version_info.major, sys.version_info.minor
    )
    raise Exception(version_error)
import argparse
import configparser
import os
import zulip

from pathlib import Path

from lib.common import stream_validator, exit_immediately

# Most of the heavy lifting is done by the following modules:

from lib.populate import populate_all, populate_incremental

from lib.website import build_website

from lib.sitemap import build_sitemap

from lib.assets import download_all

try:
    import settings
except ModuleNotFoundError:
    # TODO: Add better instructions.
    exit_immediately(
        """
    We can't find settings.py.

    Please copy default_settings.py to settings.py
    and then edit the settings.py file to fit your use case.

    For testing, you can often leave the default settings,
    but you will still want to review them first.
    """
    )

NO_DIR_ERROR_WRITE = """
We cannot find a place to write {} files.

Please run the below command:

mkdir {}"""

NO_DIR_ERROR_READ = """
We cannot find a place to read {} files.

Please run the below command:

mkdir {}

And then fetch the {:0}:

python archive.py -{}"""


def get_json_directory(for_writing):
    return get_directory(
        for_writing,
        settings.json_directory,
        "JSON",
        "t",
    )


def get_assets_directory():
    return get_directory(
        True,
        settings.assets_directory,
        "assets",
        "a",
    )


def get_html_directory():
    return get_directory(
        True,
        settings.html_directory,
        "HTML",
        "b",
    )


def get_directory(for_writing, dir: Path, name, fetch_flag):

    if not dir.exists():
        # I use posix paths here, since even on Windows folks will
        # probably be using some kinda Unix-y shell to run mkdir.
        if for_writing:
            error_msg = NO_DIR_ERROR_WRITE.format(name, dir.as_posix())
        else:
            error_msg = NO_DIR_ERROR_READ.format(name, dir.as_posix(), fetch_flag)

        exit_immediately(error_msg)

    if not dir.is_dir():
        exit_immediately(str(dir) + " needs to be a directory")

    return dir


def get_client_info():
    config_file = "./zuliprc"
    client = zulip.Client(config_file=config_file)

    # It would be convenient if the Zulip client object
    # had a `site` field, but instead I just re-read the file
    # directly to get it.
    config = configparser.RawConfigParser()
    config.read(config_file)
    zulip_url = config.get("api", "site")

    return client, zulip_url


def run():
    parser = argparse.ArgumentParser(
        description="Build an html archive of the Zulip chat."
    )
    parser.add_argument(
        "-b", action="store_true", default=False, help="Build .md files"
    )
    parser.add_argument(
        "--no-sitemap",
        action="store_true",
        default=False,
        help="Don't build sitemap files",
    )
    parser.add_argument(
        "-t", action="store_true", default=False, help="Make a clean json archive"
    )
    parser.add_argument(
        "-i",
        action="store_true",
        default=False,
        help="Incrementally update the json archive",
    )
    parser.add_argument(
        "-a",
        action="store_true",
        default=False,
        help="Download assets (images, videos, ...) from the Zulip server",
    )

    results = parser.parse_args()

    if results.t and results.i:
        print("Cannot perform both a total and incremental update. Use -t or -i.")
        exit(1)

    if not (results.t or results.i or results.a or results.b):
        print("\nERROR!\n\nYou have not specified any work to do.\n")
        parser.print_help()
        exit(1)

    json_root = get_json_directory(for_writing=results.t)

    # The directory where this archive.py is located
    repo_root = os.path.dirname(os.path.realpath(__file__))

    if results.a:
        assets_root = get_assets_directory()

    if results.b:
        md_root = get_html_directory()

    if results.t or results.i:
        is_valid_stream_name = stream_validator(settings)

    client, zulip_url = get_client_info()

    if results.t:
        populate_all(
            client,
            json_root,
            is_valid_stream_name,
        )

    elif results.i:
        populate_incremental(
            client,
            json_root,
            is_valid_stream_name,
        )

    if results.a:
        download_all(client, zulip_url, json_root, assets_root)

    if results.b:
        build_website(
            json_root,
            md_root,
            settings.site_url,
            settings.html_root,
            settings.title,
            zulip_url,
            settings.zulip_icon_url,
            repo_root,
            settings.page_head_html,
            settings.page_footer_html,
        )
        if not results.no_sitemap:
            build_sitemap(settings.site_url, md_root.as_posix(), md_root.as_posix())


if __name__ == "__main__":
    run()
