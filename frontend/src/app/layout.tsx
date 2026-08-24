import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { SiteNav } from "@/components/SiteNav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "rap-flow — turn vocal flow into percussion",
  description:
    "rap-flow separates a song's vocals with Demucs, detects syllable onsets, and renders them as a percussion track mapped to the song's grid.",
  openGraph: {
    title: "rap-flow — turn vocal flow into percussion",
    description:
      "Separate vocals with Demucs, detect syllable onsets, and render them as a percussion track locked to the song's grid.",
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-black text-white font-sans">
        <SiteNav />
        {children}
      </body>
    </html>
  );
}
