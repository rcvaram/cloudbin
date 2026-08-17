Act as a senior security engineer and Python developer. 
I am building "SecureVault," a local Python daemon on Ubuntu that automatically encrypts deleted
or intentionally moved files and uploads them to Google Drive for secure archiving.

Core Architecture & Rules:

No sync: The system is an encrypted, remote recycle bin. Files go up automatically; they only come down via manual user restoration.

File-level encryption: Files must be encrypted individually (not bundled into tarballs) to allow for single-file restoration and to save disk space during processing.

Readable structure: The original filenames (appended with .enc) and the folder tree structure must be preserved in Google Drive so the archive can be easily browsed from a mobile device.

Fail-safe processing: The local copy of a file must never be deleted until the encrypted version is successfully uploaded and verified in the cloud.


Directoy Structure

secure-vault/
├── requirements.txt         
├── main.py                  
├── setup.py
├── keys/                    
│   └── vault.key            
├── data/
│   └── vault.sqlite         
└── vault/                   
    ├── __init__.py
    ├── cli.py               
    ├── worker.py            
    ├── monitors/
    │   ├── __init__.py
    │   ├── trash_watcher.py 
    │   └── drop_watcher.py  
    ├── crypto/
    │   ├── __init__.py
    │   ├── key_manager.py   
    │   └── engine.py        
    └── storage/
        ├── __init__.py
        ├── database.py      
        └── rclone.py


Component Details:

Storage (vault/storage/)

database.py: Uses SQLite (data/vault.sqlite). Tracks id, original_filename, original_abspath, cloud_relpath, size_bytes, sha256_hash, status (PENDING, ARCHIVED, RESTORED, DELETED), source_type (TRASH or VAULTDROP), and timestamps. Needs CRUD functions.

rclone.py: Uses Python's subprocess to call the local rclone binary. Needs upload_to_drive(local, remote) and verify_cloud_file(remote).

Crypto (vault/crypto/)

engine.py: Uses cryptography.fernet. Must read/encrypt files in memory-efficient chunks (e.g., 64KB) and calculate the SHA-256 hash simultaneously. Needs encrypt_file and decrypt_file functions.

key_manager.py: Safely loads the symmetric AES-256 key.

Monitors & Worker (vault/monitors/ and vault/worker.py)

drop_watcher.py / trash_watcher.py: Uses the watchdog library to monitor ~/VaultDrop and ~/.local/share/Trash/files/.

worker.py: The orchestrator. It uses pathlib to crawl dropped folders, determine relative paths, and coordinate the hash -> encrypt -> upload -> verify -> database commit -> delete local process.

CLI (vault/cli.py)

Provides terminal commands for the user to interact with the system (e.g., vault search, vault restore <id>).

Please acknowledge these instructions. I will ask you to generate the code module by module.
