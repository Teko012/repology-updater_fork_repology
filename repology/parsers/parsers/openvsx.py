# Copyright (C) 2024 Gavin John <gavinnjohn@gmail.com>
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

import os
import re
from typing import Any, Iterable

from libversion import version_compare

from repology.package import LinkType, PackageFlags
from repology.packagemaker import NameType, PackageFactory, PackageMaker
from repology.parsers import Parser
from repology.parsers.json import iter_json_list


_LICENSE_EXPRESSION = re.compile('[A-Za-z0-9.+() -]+')


def _is_license_expression(value: str | None) -> bool:
    return value is not None and _LICENSE_EXPRESSION.fullmatch(value) is not None and not value.startswith('SEE ') and value != 'UNLICENSED'


def _is_better_entry(entry: dict[str, Any], current: dict[str, Any]) -> bool:
    res = version_compare(entry['version'], current['version'])
    return res > 0 or (res == 0 and entry['targetPlatform'] == 'universal')


class OpenVSXParser(Parser):
    def iter_parse(self, path: str, factory: PackageFactory) -> Iterable[PackageMaker]:
        # there's an entry for each target platform, and these may have
        # different versions; pick the latest one, preferring universal
        entries: dict[str, dict[str, Any]] = {}

        for pagename in os.listdir(path):
            if not pagename.endswith('.json'):
                continue

            for extension in iter_json_list(os.path.join(path, pagename), ('extensions', None)):
                key = f'{extension["namespace"]}.{extension["name"]}'.lower()

                # only keep what's needed, the full entries are huge
                entry = {
                    'namespace': extension['namespace'],
                    'name': extension['name'],
                    'displayName': extension.get('displayName'),
                    'version': extension['version'],
                    'preRelease': extension.get('preRelease', False),
                    'targetPlatform': extension.get('targetPlatform', 'universal'),
                    'description': extension.get('description'),
                    'homepage': extension.get('homepage'),
                    'repository': extension.get('repository'),
                    'bugs': extension.get('bugs'),
                    'license': extension.get('license'),
                    'download': extension.get('files', {}).get('download'),
                    'manifest': extension.get('files', {}).get('manifest'),
                }

                if key not in entries or _is_better_entry(entry, entries[key]):
                    entries[key] = entry

        for key, entry in sorted(entries.items()):
            with factory.begin(key) as pkg:
                namespace = entry['namespace']
                name = entry['name']
                pkg.add_name(f'{namespace}.{name}', NameType.OPENVSX_NAMESPACE_DOT_NAME)
                pkg.add_name(f'{namespace}/{name}', NameType.OPENVSX_NAMESPACE_SLASH_NAME)
                pkg.add_name(entry['displayName'] or name, NameType.OPENVSX_DISPLAYNAME)
                pkg.set_version(entry['version'])
                pkg.set_flags(PackageFlags.DEVEL, entry['preRelease'])
                pkg.set_summary(entry['description'])
                pkg.add_maintainers(f'{namespace}@openvsx')

                pkg.add_links(LinkType.UPSTREAM_HOMEPAGE, entry['homepage'])
                pkg.add_links(LinkType.UPSTREAM_REPOSITORY, entry['repository'])
                # package.json bugs field may be an email instead of an url
                if entry['bugs'] and not entry['bugs'].startswith('mailto:'):
                    pkg.add_links(LinkType.UPSTREAM_ISSUE_TRACKER, entry['bugs'])

                # package.json license field, which may also be a reference to a file,
                # an url, a localization placeholder or a proprietary license notice
                if _is_license_expression(entry['license']):
                    pkg.add_licenses(entry['license'])

                if not entry['download']:
                    continue

                pkg.add_links(LinkType.PROJECT_DOWNLOAD, entry['download'])
                pkg.add_links(LinkType.PACKAGE_RECIPE_RAW, entry['manifest'])

                yield pkg
