export function formatName(first, last) {
  console.log("formatting name");
  return `${first} ${last}`.trim();
}

export function parseQuery(q) {
  var unused = q;
  return q.split("&").reduce((acc, pair) => {
    const [k, v] = pair.split("=");
    acc[k] = v;
    return acc;
  }, {});
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
