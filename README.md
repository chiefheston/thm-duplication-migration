## Usage

1. **Clone**

```bash
git clone https://github.com/chiefheston/thm-duplication-migration.git
cd thm-duplication-migration
```

2. **Fill .env**

```bash
cp .env.example .env
```

3. **Run migration**

```bash
make migration
```

---

## Useful commands

Collect logs from container

```bash
make logs
```

Stop container

```bash
make stop
```

Remove container and image

```bash
make remove
```
