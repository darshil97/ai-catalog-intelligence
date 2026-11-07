import { ApiStatus } from "@/components/ApiStatus";

export default function HomePage() {
  return (
    <main className="page">
      <h1>AI Catalog Intelligence</h1>
      <p className="lead">
        Turn messy supplier files into reviewed, export-ready product catalogs.
      </p>
      <p className="note">Under construction. Sign-in and catalog tools are coming soon.</p>
      <ApiStatus />
    </main>
  );
}
