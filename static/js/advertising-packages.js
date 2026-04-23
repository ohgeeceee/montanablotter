import React from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);

function readBootstrap() {
  const script = document.getElementById("advertising-packages-data");
  if (!script) {
    return { packages: [], salesPhone: "", salesTel: "" };
  }

  try {
    const payload = JSON.parse(script.textContent || "{}");
    return {
      packages: Array.isArray(payload.packages) ? payload.packages : [],
      salesPhone: payload.salesPhone || "",
      salesTel: payload.salesTel || "",
    };
  } catch {
    return { packages: [], salesPhone: "", salesTel: "" };
  }
}

function FeatureItem({ children, featured }) {
  return html`
    <li className="flex items-start gap-3 text-sm text-slate-600">
      <span
        className=${`mt-0.5 inline-flex h-5 w-5 flex-none items-center justify-center rounded-full text-[11px] font-black ${
          featured ? "bg-amber-100 text-amber-700" : "bg-slate-200 text-slate-700"
        }`}
      >
        +
      </span>
      <span>${children}</span>
    </li>
  `;
}

function PackageCard({ pkg }) {
  const featured = Boolean(pkg.highlight);
  const cardClass = featured
    ? "relative flex h-full flex-col rounded-[28px] border border-amber-400 bg-white p-6 shadow-lg ring-1 ring-amber-200 transition duration-200 hover:-translate-y-1 hover:shadow-xl"
    : "relative flex h-full flex-col rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm transition duration-200 hover:-translate-y-1 hover:shadow-lg";
  const buttonClass = featured
    ? "mt-6 inline-flex items-center justify-center rounded-full bg-amber-500 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-slate-950 transition hover:bg-amber-400"
    : "mt-6 inline-flex items-center justify-center rounded-full bg-slate-950 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800";

  return html`
    <article className=${cardClass}>
      ${featured
        ? html`
            <div className="absolute right-5 top-5 inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-amber-700">
              Best Value
            </div>
          `
        : null}

      <div className="pr-24">
        <p className=${`text-[11px] font-black uppercase tracking-[0.22em] ${featured ? "text-amber-700" : "text-slate-500"}`}>
          ${pkg.type}
        </p>
        <h3 className="mt-3 text-2xl font-black tracking-tight text-slate-950">${pkg.name}</h3>
      </div>

      <div className="mt-8 border-t border-slate-200 pt-6">
        <div className="flex items-end gap-2">
          <p className="text-5xl font-black tracking-tight text-slate-950">${pkg.price}</p>
          <span className="pb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">Monthly</span>
        </div>
        <p className="mt-2 text-sm text-slate-500">Package Deal: ${pkg.annual}</p>
      </div>

      <ul className="mt-6 space-y-3">
        ${(pkg.features || []).map(
          (feature) => html`<${FeatureItem} key=${feature} featured=${featured}>${feature}</${FeatureItem}>`
        )}
      </ul>

      <div className="mt-auto pt-6">
        <a href=${pkg.checkoutUrl} className=${buttonClass}>
          ${pkg.cta}
        </a>
      </div>
    </article>
  `;
}

function SalesCallout({ salesPhone, salesTel }) {
  if (!salesTel) return null;

  return html`
    <div className="mt-6 rounded-[28px] border border-slate-200 bg-slate-50 p-5 shadow-sm">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-500">Mobile Priority</p>
          <h3 className="mt-2 text-lg font-black text-slate-950">Tap-to-call your sales desk in one touch</h3>
          <p className="mt-1 text-sm text-slate-600">
            Most bail-bond buyers convert on mobile. Use direct call routing to close placement decisions faster.
          </p>
        </div>
        <a
          href=${salesTel}
          className="inline-flex items-center justify-center rounded-full bg-slate-950 px-5 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800"
        >
          Call ${salesPhone || "Sales"}
        </a>
      </div>
    </div>
  `;
}

function AdvertisingPackages({ packages, salesPhone, salesTel }) {
  return html`
    <section className="bg-white">
      <div className="bg-slate-950 px-6 py-8 sm:px-8 lg:px-10">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <div className="inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-[10px] font-black uppercase tracking-[0.2em] text-amber-300">
              Advertising Packages
            </div>
            <h2 className="mt-4 text-3xl font-black tracking-tight text-white sm:text-4xl">
              High-intent inventory built for Montana legal-response advertisers
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
              Compare county exclusivity, persistent sidebar coverage, statewide header visibility, and the featured
              bundle designed to own the highest-conversion surfaces on MontanaBlotter.com.
            </p>
          </div>
          <a
            href="/bail-bonds"
            className="inline-flex items-center justify-center rounded-full border border-slate-700 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-slate-200 transition hover:border-amber-400 hover:text-white"
          >
            View Live Directory
          </a>
        </div>
      </div>

      <div className="bg-gradient-to-b from-slate-50 via-white to-slate-50 px-6 py-8 sm:px-8 lg:px-10">
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-4">
          ${packages.map((pkg) => html`<${PackageCard} key=${pkg.id} pkg=${pkg} />`)}
        </div>
        <${SalesCallout} salesPhone=${salesPhone} salesTel=${salesTel} />
      </div>
    </section>
  `;
}

const rootNode = document.getElementById("advertising-packages-root");

if (rootNode) {
  const bootstrap = readBootstrap();
  createRoot(rootNode).render(
    html`<${AdvertisingPackages} packages=${bootstrap.packages} salesPhone=${bootstrap.salesPhone} salesTel=${bootstrap.salesTel} />`
  );
}
