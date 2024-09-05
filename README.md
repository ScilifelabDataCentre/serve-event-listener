# Serve Event Listener

The Serve Event Listener is a service designed to monitor changes in Kubernetes and automatically update corresponding `AppInstance` and `AppStatus` objects in the Serve platform. This README provides detailed instructions on how to run the service on both a host machine and within a Docker container.

## Host Machine Setup

### Prerequisites
Make sure you have the following prerequisites installed on your host machine:
- Python 3.x
- Kubernetes cluster configuration file (`cluster.conf`)

### Configuration
Set the `KUBECONFIG` environment variable to point to your Kubernetes cluster configuration:

```bash
export KUBECONFIG=/path/to/cluster.conf
export USERNAME=<serve admin username>
export PASSWORD=<serve admin password>
export BASE_URL=<serve URL (no trailing slash)>
export TOKEN_API_ENDPOINT=<end point for fetching token> (optional, is set to BASE_URL + "/api/v1/token-auth/" if not defined)
export APP_STATUS_API_ENDPOINT=<end point for status updates> (optional, is set to BASE_URL + "/api/v1/app-status/" if not defined)
```

To retrieve additional log messages, set:

```bash
export DEBUG=True
export TEST_LOG_STREAM=sys.stdout
```

### Running the Service
Navigate to the project directory and execute the following command to run the service:

```bash
python3 ./serve_event_listener/main.py --namespace <some-namespace> --label-selector <some label selector>
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
docker run --rm -v $PWD/cluster.conf:/home/serve/cluster.conf
    --env KUBECONFIG=/home/serve/cluster.conf
    --env USERNAME=admin@test.com
    ...(set all env from above)
     <image name>:<image tag>
     --namespace <some-namespace>
     --label-selector <some label selector>
```

## Program Arguments

The following are the main function arguments that can be passed to run the program:

- `--namespace`: Kubernetes namespace to watch (default: `default`).
- `--label-selector`: Label selector for filtering pods (default: `type=app`).
