# FileStudio

FileStudio is a browser-based local file management tool for Windows and LAN environments. It allows you to browse folders, preview files, edit text, upload/download content, and manage a local storage root from a simple web interface.

## Highlights

- Browser-based file explorer with a clean interface
- Admin login and guest preview mode
- Text editing and file creation
- Image preview and file download
- Office document and PDF preview
- Archive listing support
- Custom background and theme settings
- Suitable for local network access via a browser

## Project status

This project is designed as a lightweight, portable workstation utility for quick file operations and local content review, without requiring a separate backend service or database.

## Quick start

1. Install Python 3.13+
2. Install dependencies:

```bash
py -3.13 -m pip install -r requirements.txt
```

3. Start the app:

```bash
py -3.13 server.py
```

4. Open the local URL shown in the terminal, typically:

```text
http://127.0.0.1:1111/
```

You can also access it from other devices on the same LAN by using the displayed local IP address.

## Default admin access

The default admin account is:

- Username: `admin`
- Password: `Axiao852`

The password can be changed with the command-line option:

```bash
py -3.13 server.py --password "your-new-password"
```

## Features

### File browsing
- Browse directories from the selected root path
- View file metadata and timestamps
- Open and switch storage roots

### Editing and preview
- Edit plain text files directly in the browser
- Preview images, PDFs, Office documents, and archives
- Download files and folders
- Upload new files into the selected root

### Security model
- Admin mode has full write access
- Guest mode is read-only for preview and download
- Path restrictions prevent writes outside the configured root directory

## Project structure

```text
.
├── core.py          # Shared file-type and document logic
├── server.py        # HTTP server and app entry point
├── web/             # Frontend assets
├── requirements.txt # Python dependencies
├── build.bat        # PyInstaller packaging helper
├── FileStudio.spec  # PyInstaller spec file
├── icon.ico         # App icon
├── selftest.py      # Local verification script
├── make_icon.py     # Icon generation helper
└── README.md
```

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Notes

This is a local-first tool intended for convenience, internal file management, and quick preview tasks. It is best suited for trusted environments and controlled local or LAN access.


