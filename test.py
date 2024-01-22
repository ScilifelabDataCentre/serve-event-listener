from kubernetes import client, config, watch

config.load_kube_config()
k8s_api = client.AppsV1Api()
k8s_watch = watch.Watch()

label_selector = "type=app"
namespace="default"

for event in k8s_watch.stream(k8s_api.list_namespaced_deployment, namespace=namespace):
    pod = event["object"]
    print(pod)
    
    