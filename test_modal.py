import modal

app = modal.App("test-app")

@app.function()
@modal.web_endpoint(method="POST")
def web_trigger(data):
    return {}
