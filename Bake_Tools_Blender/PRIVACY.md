# Privacy

Bake Groups Tool for Blender does not send scene geometry, object names, file
paths, account names, host names, email addresses, or support packages.

## Optional installation statistics

On first opening the manager, Blender asks whether the artist wants to enable
anonymous installation statistics. The default choice is **No** and no request
is made before explicit consent. The option can later be changed in About.

When enabled, one event is sent for the first run of each plugin version:

- a random client UUID;
- product (`blender`);
- plugin and Blender versions;
- event (`install` or `update`);
- interface language;
- operating-system platform;
- telemetry schema version.

The response is stored in a Google Form owned by Veteraros AI. Google receives
normal network metadata such as the request IP address. The local UUID and
consent are stored in `~/.bake_groups_tool/client.json`. Deleting that file
removes the local identifier and consent choice.

## Updates

Update checks occur only when the artist presses Check in About. Download and
installation require an additional explicit Update click. Release archives are
downloaded from GitHub and accepted only when their SHA-256 matches the public
manifest.
