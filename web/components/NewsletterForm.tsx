"use client";

export function NewsletterForm() {
  // TODO: there is no newsletter endpoint in api/ yet. Send `email` to it once one exists.
  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
  }

  return (
    <form className="sf-form" onSubmit={onSubmit}>
      <label htmlFor="sf-email" className="sr-only">
        Email address
      </label>
      <input id="sf-email" name="email" type="email" autoComplete="email" placeholder="you@example.com" required />
      <button type="submit">Subscribe</button>
    </form>
  );
}
