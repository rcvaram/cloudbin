# Contributing to CloudBin

Thank you for your interest in contributing to CloudBin.

CloudBin is developed openly, and contributions are welcome.

## Contribution Workflow

CloudBin uses the standard **fork and pull request** workflow.

### 1. Fork the repository

Fork the CloudBin repository to your own GitHub account.

### 2. Clone your fork

```bash
git clone https://github.com/YOUR_USERNAME/cloudbin.git
cd cloudbin
```

### 3. Create a branch

```bash
git checkout -b feature/my-change
```

### 4. Set up the development environment

```bash
python3 -m venv venv
source venv/bin/activate

python -m pip install -e .
```

### 5. Make your changes

Keep changes focused and easy to review.

Add or update tests when appropriate.

### 6. Run the tests

```bash
python -m pytest
```

Make sure the existing test suite passes before opening a pull request.

### 7. Push your branch

```bash
git push origin feature/my-change
```

### 8. Open a Pull Request

Open a Pull Request from your fork to the CloudBin repository.

Please include:

* What you changed
* Why you changed it
* Tests performed
* Any relevant considerations

## Code Principles

CloudBin values:

* Simple designs
* Clear and maintainable code
* Small, focused changes
* Good test coverage
* Security-conscious decisions
* Minimal unnecessary dependencies

## Security

Please do not disclose security vulnerabilities through public GitHub issues or pull requests.

See [SECURITY.md](../SECURITY.md) for security reporting.

## Questions and Ideas

Issues and discussions are welcome for:

* Bug reports
* Feature ideas
* Documentation improvements
* Security improvements
* Cloud provider integrations
* Testing
* Performance improvements

Thank you for helping build CloudBin.
