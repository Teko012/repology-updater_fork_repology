# Copyright (C) 2024 Dmitry Marakasov <amdmi3@amdmi3.ru>
#
# This file is part of repology
#
# repology is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# repology is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with repology.  If not, see <http://www.gnu.org/licenses/>.

import json
import os
import time
import xml.etree.ElementTree
from itertools import count

import requests

from repology.atomic_fs import AtomicDir
from repology.fetchers import PersistentData, ScratchDirFetcher
from repology.fetchers.http import PoliteHTTP
from repology.logger import Logger


_SITEMAP_NS = '{http://www.sitemaps.org/schemas/sitemap/0.9}'


# The query API returns latest version of each extension, one entry per
# target platform, ordered by internal extension id. It's paginated by
# offset and there's no way to get a consistent snapshot, so the result
# is validated against the sitemap, which lists all active extensions in
# a single document, and any inconsistency fails the fetch.
class OpenVSXFetcher(ScratchDirFetcher):
    def __init__(self, url: str, sitemap_url: str | None = None, page_size: int = 1000, fetch_timeout: int = 120, fetch_delay: int | None = None, max_tries: int = 5, retry_delay: int = 5) -> None:
        self.url = url
        self.sitemap_url = sitemap_url
        self.page_size = page_size
        self.do_http = PoliteHTTP(timeout=fetch_timeout, delay=fetch_delay)
        self.max_tries = max_tries
        self.retry_delay = retry_delay

    def _do_fetch_retry(self, url: str, logger: Logger) -> str:
        num_try = 1
        while True:
            logger.log(f'getting {url}' if num_try == 1 else f'getting {url} (try #{num_try})')

            try:
                return self.do_http(url).text
            except (requests.ConnectionError, requests.Timeout) as e:
                if num_try >= self.max_tries:
                    raise
                logger.log(f'failed to fetch {url}: {e}, retrying after delay...', Logger.ERROR)
                time.sleep(self.retry_delay)
                num_try += 1

    def _fetch_sitemap(self, url: str, logger: Logger) -> set[str]:
        root = xml.etree.ElementTree.fromstring(self._do_fetch_retry(url, logger))

        extensions = set()
        for loc in root.iterfind(f'{_SITEMAP_NS}url/{_SITEMAP_NS}loc'):
            if loc.text and '/extension/' in loc.text:
                namespace, name = loc.text.rstrip('/').rsplit('/', 2)[-2:]
                extensions.add(f'{namespace}.{name}'.lower())

        if not extensions:
            raise RuntimeError('sitemap lists no extensions')

        return extensions

    def _do_fetch(self, statedir: AtomicDir, persdata: PersistentData, logger: Logger) -> bool:
        # fetched before the query, so an extension added during the
        # query cannot appear here without appearing there as well
        expected = self._fetch_sitemap(self.sitemap_url, logger) if self.sitemap_url else None

        page_counter = count()
        total_size: int | None = None
        num_fetched = 0
        fetched: set[str] = set()

        while total_size is None or num_fetched < total_size:
            text = self._do_fetch_retry(f'{self.url}?includeAllVersions=false&size={self.page_size}&offset={num_fetched}', logger)
            data = json.loads(text)

            if total_size is None:
                total_size = data['totalSize']
                logger.log(f'{total_size} entries to fetch')
            elif data['totalSize'] != total_size:
                raise RuntimeError(f'total size changed from {total_size} to {data["totalSize"]} during fetch, registry has been modified')

            extensions = data['extensions']
            if not extensions:
                raise RuntimeError(f'got empty page at offset {num_fetched} of {total_size}')

            with open(os.path.join(statedir.get_path(), f'{next(page_counter)}.json'), 'w', encoding='utf-8') as pagefile:
                pagefile.write(text)
                pagefile.flush()
                os.fsync(pagefile.fileno())

            num_fetched += len(extensions)
            fetched.update(f'{extension["namespace"]}.{extension["name"]}'.lower() for extension in extensions)

        logger.log(f'fetched {num_fetched} entries for {len(fetched)} extensions')

        # removal of an extension during the fetch shifts the following
        # entries by one, so one of them is skipped; this catches that
        if expected is not None and (missing := expected - fetched):
            raise RuntimeError(f'{len(missing)} extension(s) listed in sitemap were not fetched, e.g. {", ".join(sorted(missing)[:5])}')

        return True
