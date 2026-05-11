import requests

task_id = "8bab382e-d663-43dc-81f7-28032865c33c"
url = f"http://localhost:5001/api/v1/publisher/retry/{task_id}"

response = requests.post(url)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
