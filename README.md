# Kubernetes Event Listener by Serve

The Kubernetes Event Listener by Serve is a service designed to monitor changes in Kubernetes and automatically send the latest information to another application for processing. This README provides detailed instructions on how to run the service on both a host machine and within a Docker container.

## Development

In this repo, we work [Trunk based](https://www.toptal.com/software/trunk-based-development-git-flow), which means that we bypass the dev branch.

Create short-lived branches from main:
```bash
git checkout main
git pull
git checkout -b feature/xyz
```

Keep your branch fresh:
```bash
git fetch
git rebase origin/main
```

Verify that the code adheres to the style guidelines and code analysis rules:
```bash
flake8 .
isort .
black . --check
hadolint Dockerfile
```

When the work is ready to be merged to main, create a PR to merge your feature branch into the main branch.

To release a new version of the event-listener, first tag the latest commit. When the tag is pushed to origin, a github action is run automatically and publishes a new package which is a docker image called event-listener.

## Host Machine Setup

### Prerequisites
Make sure you have the following prerequisites installed on your host machine:
- Python 3.x
- Kubernetes cluster configuration file (`cluster.conf`)

### Configuration
Set the `KUBECONFIG` environment variable to point to your Kubernetes cluster configuration:

```bash
export KUBECONFIG=/path/to/cluster.conf
export BASE_URL=<Retriever URL (Serve) (no trailing slash)>
export TOKEN_API_ENDPOINT=<end point for fetching token> (optional, is set to BASE_URL + "/api/v1/token-auth/" if not defined)
export APP_STATUS_API_ENDPOINT=<end point for status updates> (optional, is set to BASE_URL + "/api/v1/app-status/" if not defined)
export USERNAME=<admin username (Serve)>
export PASSWORD=<admin password (Serve)>
```

To enable URL probing, set the environment variable APP_PROBE_STATUSES to the status codes for which the probing should run.
This is currently only available for the Running and Deleted statuses.

```bash
export APP_PROBE_STATUSES=Running,Deleted
```

An environment variable APP_PROBE_APPS controls the app types that the URL probing controls. Currently only available for shiny and shiny-proxy (default).

```bash
export APP_PROBE_STATUSES=shiny,shiny-proxy
```

There are additional environment variables with sensible defaults that control URL resolution. See app_urls.py for more information.

To retrieve additional log messages during development, set:

```bash
export DEBUG=True
export TEST_LOG_STREAM=sys.stdout
```

### Running the Service
Navigate to the project directory and execute the following command to run the service:

```bash
python3 -m serve_event_listener.main --namespace <some-namespace> --label-selector <some label selector>
```

## Docker Container Setup

### Build the Docker Image
Build the Docker container image using the following command. Replace `<image name>` and `<image tag>` with your desired values.

```bash
docker build -t <image name>:<image tag> .
```

### Running the Docker Container
Run the Docker container with the mounted `cluster.conf` file and provide necessary environment variables:

```bash
docker run --rm -v $PWD/cluster.conf:/app/cluster.conf \
    --env KUBECONFIG=/app/cluster.conf \
    --env USERNAME=admin@test.com \
    ...(set all env from above) \
     <image name>:<image tag> \
     --namespace <some-namespace> \
     --label-selector <some label selector>
```

## Program Arguments

The following are the main function arguments that can be passed to run the program:

- `--namespace`: Kubernetes namespace to watch (default: `default`).
- `--label-selector`: Label selector for filtering pods (default: `type=app`).

## Testing

This project contains both unit tests and integration tests.

### Running the unit tests

```bash
python -m unittest discover -s tests/unit/
```

### Running the integration tests

To run the integration tests, the variable RUN_INTEGRATION_TESTS must be set to 1.

1. Start the target service (as defined by BASE_URL). Ensure that KUBECONFIG is set or can be resolved.

2. Then run the integration tests:
```bash
RUN_INTEGRATION_TESTS=1 python -m unittest discover -v -s tests/integration/
```

If you have an existing app available for testing in your target k8s environment, then you can use it to run additional tests using env var PROBE_RELEASE, like so:

```bash
RUN_INTEGRATION_TESTS=1 PROBE_RELEASE=<app-release> NAMESPACE_UNDER_TEST=default python -m unittest discover -v -s tests/integration/
```

### Pytest

If you instead prefer to use Pytest for nicer output etc:

```bash
pip install pytest
pytest tests/unit -v
RUN_INTEGRATION_TESTS=1 pytest tests/integration -v
RUN_INTEGRATION_TESTS=1 PROBE_RELEASE=<app-release> pytest tests/integration -v
```
