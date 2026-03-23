import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AutoAI Builder – Train ML Models Automatically",
  description: "Upload a CSV and let AutoAI Builder train the best ML model for you.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
        <p className="fixed bottom-2 left-0 right-0 text-center text-[11px] text-slate-500 pointer-events-none z-50">
          Developed by Yash Tapase
        </p>
      </body>
    </html>
  );
}
