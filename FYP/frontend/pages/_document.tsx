import { Html, Head, Main, NextScript } from 'next/document'

// runs server-side only — no NEXT_PUBLIC_* env vars available here
export default function Document() {
  return (
    <Html lang="en">
      <Head>
        <meta name="theme-color" content="#060a12" />
        <meta
          name="description"
          content="ANPR — Automatic Number Plate Recognition Security Dashboard"
        />
        {/*
          next/font in _app.tsx self-hosts Figtree + JetBrains Mono — no
          external font link needed here. DNS prefetching the API URL is done
          via next.config.js headers or a <Head> tag inside individual pages.
        */}
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  )
}