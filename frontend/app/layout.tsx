/**
 * Root layout: loads the font and global styles, and wraps every screen in the toast provider and sidebar shell.
 */

import type { Metadata, Viewport } from "next";
import { Source_Sans_3 } from "next/font/google";
import { AppShell } from "@/components/AppShell";
import { ToastProvider } from "@/components/ui/Toast";
import "./globals.css";

const sourceSans = Source_Sans_3({
  subsets: ["latin"],
  variable: "--font-source-sans",
  display: "swap",
});

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#154936",
};

export const metadata: Metadata = {
  title: { default: "BusinessSense", template: "%s · BusinessSense" },
  description: "Invoice management and purchasing intelligence",
};

/** The HTML page every screen is rendered inside. */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sourceSans.variable} antialiased`}>
        <ToastProvider>
          <AppShell>{children}</AppShell>
        </ToastProvider>
      </body>
    </html>
  );
}
