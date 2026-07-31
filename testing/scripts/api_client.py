class APIClient:
    def __init__(self, base_url, api_key=None):
        self.base_url = base_url
        self.api_key = api_key
        self.session = None

    def connect(self):
        self.session = {"connected": True, "base": self.base_url}
        return self

    def get(self, endpoint, params=None):
        if not self.session:
            raise RuntimeError("Not connected")
        return {"status": 200, "url": f"{self.base_url}/{endpoint}", "params": params}

    def post(self, endpoint, data=None):
        if not self.session:
            raise RuntimeError("Not connected")
        return {"status": 201, "url": f"{self.base_url}/{endpoint}", "data": data}

    def put(self, endpoint, data=None):
        if not self.session:
            raise RuntimeError("Not connected")
        return {"status": 200, "url": f"{self.base_url}/{endpoint}", "data": data}

    def delete(self, endpoint):
        if not self.session:
            raise RuntimeError("Not connected")
        return {"status": 204, "url": f"{self.base_url}/{endpoint}"}

    def close(self):
        self.session = None


def build_url(base, path, params=None):
    url = f"{base}/{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{query}"
    return url


def parse_response(response):
    if response.get("status", 0) >= 400:
        raise ValueError(f"HTTP Error: {response['status']}")
    return response
