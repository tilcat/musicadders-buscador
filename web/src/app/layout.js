import { DM_Sans, DM_Mono } from "next/font/google";
import "@/app/globals.css";

const dmSans = DM_Sans({
  subsets: ["latin"],
  axes: ["opsz"],
  weight: "variable",
  variable: "--font-sans",
  display: "swap",
});

const dmMono = DM_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata = {
  title: "Musicadders · Buscador de placements",
  description: "Busca en qué playlists aparece un ISRC en todas las DSPs vía Soundcharts.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="es" className={`${dmSans.variable} ${dmMono.variable} h-full`}>
      <body className="h-full antialiased">{children}</body>
    </html>
  );
}
