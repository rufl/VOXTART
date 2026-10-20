<!--
Keep this document short & concise,
linking to external resources instead of including content in-line.
See 'release/text/readme.html' for the end user read-me.
-->

Blender
=======

Blender is the free and open source 3D creation suite.
It supports the entirety of the 3D pipeline—modeling, rigging, animation, simulation, rendering, compositing,
motion tracking and video editing.

![Blender screenshot](https://code.blender.org/wp-content/uploads/2018/12/springrg.jpg "Blender screenshot")

Project Pages
-------------

- [Main Website](https://www.blender.org)
- [Reference Manual](https://docs.blender.org/manual/en/latest/index.html)
- [User Community](https://www.blender.org/community/)

Development
-----------

- [Build Instructions](https://developer.blender.org/docs/handbook/building_blender/)
- [Code Review & Bug Tracker](https://projects.blender.org)
- [Developer Forum](https://devtalk.blender.org)
- [Developer Documentation](https://developer.blender.org/docs/)


License
-------

Blender as a whole is licensed under the GNU General Public License, Version 3.
Individual files may have a different but compatible license.

See [blender.org/about/license](https://www.blender.org/about/license) for details.

VOXTART additions
-----------------

This checkout contains the VOXTART voxel-authoring add-on, launcher, tools,
and tests alongside the Blender source tree. VOXTART-owned software is
licensed under GPL-3.0-or-later. VOXTART artwork and icons are released under
CC0-1.0.

The repository follows the [REUSE Specification](https://reuse.software/spec/)
for file-level license metadata. Run `reuse lint` from the repository root to
verify compliance. See [VOXTART-NOTICES.txt](VOXTART-NOTICES.txt) and
[VOXTART-SOURCE-CODE-OFFER.txt](VOXTART-SOURCE-CODE-OFFER.txt) for attribution,
third-party licensing, and corresponding-source details.

Offline creature package
------------------------

The distributable VOXTART tree includes the dependency-free anyCreature
engine and a Node.js runtime under `third_party/`, so the Creature chat mode
works offline without npm or network access. Leave the anyCreature root and
Node executable preferences blank to use the bundled components; explicit
development overrides are supported. See
[VOXTART-NOTICES.txt](VOXTART-NOTICES.txt),
[LICENSES/anyCreature-MIT.txt](LICENSES/anyCreature-MIT.txt),
[LICENSES/LicenseRef-Node.js.txt](LICENSES/LicenseRef-Node.js.txt), and
[third_party/VOXTART-VENDOR.lock](third_party/VOXTART-VENDOR.lock) for
credits, licenses, provenance, and checksums.
