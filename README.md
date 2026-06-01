# ansible-monitoring-plugin

Simple Ansible Callback Plugin to send events to an API for processing. Aggregates metrics by playbook, play and tasks.

Test API lives in the `api` directory and can be run by doing the following

```
uvicorn ansible_ingest_api:app --reload
```