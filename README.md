# CloudBin

### Your cloud does not need to be trusted.
### **Use it as a bin.**
![CloudBin](/home/sivaram/PycharmProjects/cloudbin/docs/CloudBinArchitecture.png)

CloudBin is a privacy-first file archival tool that lets you use cloud storage **without treating your cloud provider as trusted storage**.

Put your files in CloudBin. 
CloudBin encrypts them, archives them in the cloud, verifies the archive, and safely removes the local copy.

When you need a file again, you can restore it locally with its **original filename, folder structure, and fingerprint**.

The cloud stores the archive.
**You keep control of the files.**

---

## The Idea

Cloud storage is great for storing things.

But why should the cloud provider need to be trusted with the contents of those things?

CloudBin treats cloud storage differently:

```text
                 YOUR COMPUTER
                      │
                 Original files
                      │
                      ▼
                  ┌─────────┐
                  │ CloudBin│
                  └────┬────┘
                       │
                 Encrypt + Verify
                       │
                       ▼
                ┌──────────────┐
                │  Cloud "Bin" │
                │  Encrypted   │
                │   Archive    │
                └──────────────┘
                       │
                 Restore when
                    needed
                       │
                       ▼
                 YOUR COMPUTER
```

Your cloud provider becomes a **place to keep the encrypted archive**, not a place you have to trust with your files.

---

## What CloudBin Does

- Watches a local `VaultDrop` directory
- Encrypts files before cloud storage
- Preserves filenames and folder structure
- Creates SHA-256 fingerprints
- Uploads through `rclone crypt`
- Verifies the remote archive
- Stores archive metadata locally
- Removes the local file only after successful verification
- Allows the archive to be restored and verified locally

### The core rule

> **Never remove the local file until the cloud archive has been successfully verified.**

---

## MVP

CloudBin is currently an **early-stage MVP**.

The current MVP supports:

- Linux
- Google Drive through rclone
- Client-side encryption with `rclone crypt`
- Automatic file archival
- Integrity verification
- SQLite metadata
- CLI operation
- End-to-end tested archival workflow
- PyPI installation

---

## Quick Start

### Install

```bash
python -m pip install cloudbin
```

### Configure

```bash
rclone config
cloudbin init
```

### Start

```bash
cloudbin start
```

Then place a file into your `VaultDrop`.

```text
VaultDrop/
└── Documents/
    └── report.pdf
```

CloudBin archives it while preserving:

```text
Documents/report.pdf
```

The cloud receives the encrypted archive.

---

## Built Around a Simple Principle

CloudBin separates **storage** from **trust**.

```text
Cloud Storage
     =
Where your archive lives

CloudBin
     =
How your archive is protected,
verified and managed
```

You don't have to trust the cloud provider with your plaintext files.

---

## Security

CloudBin uses `rclone crypt` for client-side encryption.

The cloud provider stores the encrypted archive rather than the original file contents.

CloudBin also maintains file fingerprints and verifies the remote archive before completing the archival operation.

Encryption credentials remain your responsibility.

See [SECURITY.md](SECURITY.md).

---

## Architecture

```text
                 CloudBin CLI
                      │
                      ▼
               VaultDrop Watcher
                      │
                      ▼
                 Vault Worker
                  │         │
                  ▼         ▼
              SQLite      Rclone
              Metadata     Client
                            │
                            ▼
                       rclone crypt
                            │
                            ▼
                      Cloud Storage
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Future Milestones

CloudBin is starting with a simple idea: **use the cloud as an encrypted archive, not a trusted filesystem.**

The product will evolve through a few focused milestones:

### 01 — Better Archive Experience

- Reliable restore and recovery
- Archive browsing
- File history and status
- Better local verification

### 02 — Cross-Platform

- Windows support
- macOS support
- Background service
- Desktop application

### 03 — More Storage Choices

- Amazon S3
- OneDrive
- Dropbox
- S3-compatible storage
- Community-supported providers

### 04 — Privacy & Recovery

- Privacy-preserving filenames
- Metadata protection
- Archive migration
- Disaster recovery
- Import / export

### 05 — CloudBin Ecosystem

- Provider/plugin architecture
- Community integrations
- Automation
- Developer API
- Community-built extensions

> **The goal is simple: make cloud storage a place you can use without having to trust it.**

---

## Open Source

CloudBin is built in the open.

Contributions, ideas, testing, security research, documentation, and integrations are welcome.

```bash
git clone https://github.com/rcvaram/cloudbin.git
cd cloudbin

python3 -m venv venv
source venv/bin/activate

python -m pip install -e .
python -m pytest
```

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## License

MIT License.

See [LICENSE](LICENSE).

---

# CloudBin

### **Don't trust the cloud.**
### **Use it as a bin.**