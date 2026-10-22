// Proves the docs toolchain's Node rejects TLS chains through the compromised eMudhra
// intermediates (nodejs/node#66871) while still trusting normal HTTPS endpoints.
// Passes only after observing CERT_REVOKED; if the incident's test host is retired, this
// fails until the revoked-host check is dropped.

const REVOKED = "https://dv-g3b-emudhra.we-have-a-problem.com/";
const TRUSTED = "https://nodejs.org/";

console.log(`process.version=${process.version}`);

async function probe(url) {
  try {
    const res = await fetch(url, { method: "HEAD" });
    return { ok: true, detail: `HTTP ${res.status}` };
  } catch (err) {
    const cause = err.cause ?? err;
    return { ok: false, code: cause.code, detail: `${cause.code ?? ""} ${cause.message}` };
  }
}

let failed = false;

const revoked = await probe(REVOKED);
console.log(`${REVOKED} -> ${revoked.detail}`);
if (revoked.ok) {
  console.log("::error::Node accepted a chain through a revoked eMudhra intermediate");
  failed = true;
} else if (revoked.code !== "CERT_REVOKED") {
  // A DNS or connection failure never reached the certificate check, so it proves nothing.
  console.log(`::error::expected CERT_REVOKED, got ${revoked.detail}`);
  failed = true;
}

const trusted = await probe(TRUSTED);
console.log(`${TRUSTED} -> ${trusted.detail}`);
if (!trusted.ok) {
  console.log("::error::Node failed to reach a normal trusted HTTPS endpoint");
  failed = true;
}

process.exit(failed ? 1 : 0);
