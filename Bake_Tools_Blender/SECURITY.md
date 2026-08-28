# Security and update policy

- The add-on does not execute code downloaded from a branch or pull request.
- Updates use immutable GitHub Release assets referenced by the public manifest.
- A 64-character SHA-256 is mandatory before an update can be staged.
- ZIP members are checked for absolute paths and `..` traversal before extract.
- The current installation is archived before activation of an update/rollback.
- Packages are activated only during the next Blender start, before the native
  module is imported.

Report security issues privately to veteraros@gmail.com.
