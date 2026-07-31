/** Service layer still using legacy fetch — needs migration. */

const { fetchUsers, fetchPosts, fetchComments } = require("./legacy_api");

async function loadDashboard(userId) {
  const users = await fetchUsers();
  const posts = await fetchPosts(userId);
  const comments = await fetchComments(posts.id || 1);
  return { users, posts, comments };
}

async function syncData() {
  console.log("Syncing data...");
  return fetchUsers();
}

module.exports = { loadDashboard, syncData };
