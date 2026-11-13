// The product lockup (DESIGN.md): the company name, the product name in a lighter weight, and
// the warm dot that the pond scene sets on its horizon as the sun. The names share one inline
// run so the space between them is real text and the accessible name reads "LotusPond Works".
export function Wordmark({ href }: { href?: string }) {
  const lockup = (
    <>
      <span className="wordmark-name">
        LotusPond <span className="wordmark-product">Works</span>
      </span>
      <span className="wordmark-dot" />
    </>
  );
  return href ? (
    <a className="wordmark" href={href}>
      {lockup}
    </a>
  ) : (
    <span className="wordmark">{lockup}</span>
  );
}
