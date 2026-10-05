# Running the service

From this folder, run:

```bash
docker build -t tablekeeper . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper
```

The service will start on `http://0.0.0.0:8080`.
