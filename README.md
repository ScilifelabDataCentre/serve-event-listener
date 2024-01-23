# Serve Event Listner

This repo contains the code for the event listerner service. This service listens to changes in kubernetes, and updates the correponding `AppInstance` and `AppStatus` objects in Serve. 

## How to run on host machine
The service need a cluster config of some kind. This should be set as a `KUBECONFIG` env variable.
If you have a `cluster.conf` file, just as in Serve, then just set `KUBECONFIG=/path/to/cluster.conf`

Next, run the service like this

```bash
python3 ./serve_event_listener/main.py --namespace <some-namespace> --label-selector <some label selector>
```

## How to run in docker container
Build the container

```bash
docker build -t <image name>:<image tag> .
```
then run it with a mounting of the `cluster.conf` file.

```bash
docker run --rm -v $PWD/cluster.conf:/home/serve/cluster.conf --env KUBECONFIG=/home/serve/cluster.conf <image name>:<image tag> --namespace <some-namespace> --label-selector <some label selector>
```