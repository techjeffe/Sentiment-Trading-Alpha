import "./globals.css";
import GlobalRemoteControlBanner from "@/components/GlobalRemoteControlBanner";
import AutoRunCountdown from "@/components/AutoRunCountdown";
import { AnalysisProvider } from "@/lib/context/AnalysisContext";
import { GlobalNav } from "@/components/GlobalNav";

export const metadata = {
  title: "Sentiment Trading Alpha",
  description: "Sentiment-driven trading signals for 3x leveraged ETFs",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-gray-900 text-white antialiased">
        <AnalysisProvider>
          <GlobalRemoteControlBanner />
          <GlobalNav />
          {children}
          {/* Persistent countdown indicator — visible on all pages */}
          <div className="fixed bottom-4 right-4 z-50 rounded-xl border border-slate-700/60 bg-slate-900/90 backdrop-blur px-3 py-2 shadow-lg">
            <AutoRunCountdown />
          </div>
        </AnalysisProvider>
      </body>
    </html>
  );
}
