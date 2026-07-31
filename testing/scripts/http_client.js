/** New HTTP client — target for migration from legacy_fetch. */

class HttpClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
    this.connected = false;
  }

  connect() {
    this.connected = true;
    return this;
  }

  get(path) {
    if (!this.connected) throw new Error("Not connected");
    return Promise.resolve({ ok: true, status: 200, data: [] });
  }
}

module.exports = { HttpClient };
