import { AlertCircle } from "lucide-react";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

type MarketCell = {
  slug: string;
  name: string;
  display_name: string | null;
};

type JurisdictionRow = {
  id: string;
  slug: string;
  name: string;
  display_name: string | null;
  state: string;
  entity_type: string | null;
  markets: MarketCell | MarketCell[] | null;
};

function marketLabel(markets: JurisdictionRow["markets"]) {
  const market = Array.isArray(markets) ? markets[0] : markets;

  return market?.display_name ?? market?.name ?? "None";
}

export default async function CoveragePage() {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from("jurisdictions")
    .select("id, slug, name, display_name, state, entity_type, markets:market_id(slug, name, display_name)")
    .order("name", { ascending: true });

  const jurisdictions = (data ?? []) as JurisdictionRow[];

  return (
    <main className="px-5 py-6">
      <div className="mb-5">
        <h1 className="text-xl font-semibold tracking-normal text-slate-950">Coverage</h1>
        <p className="text-sm text-slate-500">B.1 authenticated read path from Supabase.</p>
      </div>

      {error ? (
        <div className="flex max-w-2xl items-start gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load jurisdictions.</p>
            <p>{error.message}</p>
          </div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="bg-slate-100 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Jurisdiction</th>
                <th className="px-3 py-2 font-medium">State</th>
                <th className="px-3 py-2 font-medium">Market</th>
                <th className="px-3 py-2 font-medium">Slug</th>
              </tr>
            </thead>
            <tbody>
              {jurisdictions.map((jurisdiction) => (
                <tr className="border-t border-slate-100" key={jurisdiction.id}>
                  <td className="px-3 py-2 font-medium text-slate-900">
                    {jurisdiction.display_name ?? jurisdiction.name}
                  </td>
                  <td className="px-3 py-2 text-slate-700">{jurisdiction.state}</td>
                  <td className="px-3 py-2 text-slate-700">{marketLabel(jurisdiction.markets)}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500">
                    {jurisdiction.slug}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
