# VoxelCraft Native

Native voxel-survival project built with Godot 4.6 and the Voxel Tools 1.6 C++ module.

## Download the playable Windows build

1. Open the repository **Actions** tab.
2. Open the latest green **Build Windows EXE** run.
3. Download the `VoxelCraftNative-Windows-x64` artifact.
4. Extract the downloaded artifact ZIP.
5. Extract the `VoxelCraftNative-Windows-x64.zip` file inside it.
6. Open `VoxelCraftNative.exe`.

You do not need to install Godot. GitHub Actions downloads the pinned custom engine and Windows export template, reconstructs and verifies the complete source archive from the text-safe files in `source/chunks/`, and creates the executable automatically.

## Source

The complete Godot project is stored as Base64 archive chunks under `source/chunks/`. The workflow joins the chunks in filename order, decodes them, verifies the expected SHA-256 checksum, and only then imports the project. The archive includes the GDScript source, scenes, original texture atlas, documentation, export preset, and license.

This is a clean-room voxel survival project and does not include Minecraft source code or Mojang assets.
