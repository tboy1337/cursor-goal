/** Legacy fetch wrapper — migrate call sites to httpClient. */

function legacyFetch(url, options = {}) {
  console.log(`Fetching ${url}`);
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ data: [] }),
    text: () => Promise.resolve(""),
  });
}

function fetchUsers() {
  return legacyFetch("/api/users");
}

function fetchPosts(userId) {
  return legacyFetch(`/api/users/${userId}/posts`);
}

function fetchComments(postId) {
  return legacyFetch(`/api/posts/${postId}/comments`);
}

module.exports = { legacyFetch, fetchUsers, fetchPosts, fetchComments };
