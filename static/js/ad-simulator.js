import React, { useEffect, useMemo, useRef, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);

const PACKAGE_DETAILS = {
  banner: {
    label: "Top Banner",
    packageId: "featured_bondsman_banner",
    dimensions: "970x250 desktop • 320x50 mobile",
    monthly: "$450/mo",
    annual: "$5,400/yr",
    summary: "First-view dominance above the arrest feed with premium statewide visibility.",
  },
  sidebar: {
    label: "Sticky Sidebar",
    packageId: "emergency_call_sidebar",
    dimensions: "300x600 desktop",
    monthly: "$300/mo",
    annual: "$3,600/yr",
    summary: "Persistent right-rail placement that stays visible during longer scroll sessions.",
  },
};

const COUNTY_FEEDS = {
  "Cascade County": [
    { name: "John Doe", charge: "Probation violation", time: "14 minutes ago" },
    { name: "Mason Cole", charge: "Failure to appear", time: "31 minutes ago" },
    { name: "Trevor Wade", charge: "Criminal possession", time: "52 minutes ago" },
  ],
  "Yellowstone County": [
    { name: "Michael Smith", charge: "Partner agency booking", time: "12 minutes ago" },
    { name: "Ryan Holt", charge: "Contempt of court", time: "28 minutes ago" },
    { name: "Derrick Lane", charge: "Bail revocation", time: "49 minutes ago" },
  ],
  "Missoula County": [
    { name: "Robert James", charge: "Criminal possession", time: "19 minutes ago" },
    { name: "Lucas Hart", charge: "Failure to appear", time: "37 minutes ago" },
    { name: "Avery Moss", charge: "Probation hold", time: "1 hour ago" },
  ],
  "Gallatin County": [
    { name: "Anthony Reed", charge: "Partner agency booking", time: "17 minutes ago" },
    { name: "Dylan Brooks", charge: "Probation violation", time: "41 minutes ago" },
    { name: "Shawn Pierce", charge: "Missed hearing", time: "58 minutes ago" },
  ],
};

const THEME_OPTIONS = {
  legal_navy: {
    label: "Legal Navy",
    bgFrom: "#0f172a",
    bgTo: "#1e293b",
    surface: "#ffffff",
    accent: "#f59e0b",
    accentSoft: "#fbbf24",
    text: "#f8fafc",
    muted: "#cbd5e1",
    ink: "#0f172a",
  },
  court_slate: {
    label: "Court Slate",
    bgFrom: "#111827",
    bgTo: "#334155",
    surface: "#ffffff",
    accent: "#38bdf8",
    accentSoft: "#7dd3fc",
    text: "#f8fafc",
    muted: "#dbeafe",
    ink: "#0f172a",
  },
  urgent_crimson: {
    label: "Urgent Crimson",
    bgFrom: "#450a0a",
    bgTo: "#7f1d1d",
    surface: "#fff7ed",
    accent: "#fb7185",
    accentSoft: "#fda4af",
    text: "#fff7ed",
    muted: "#fecdd3",
    ink: "#1f2937",
  },
  frontier_green: {
    label: "Frontier Green",
    bgFrom: "#052e16",
    bgTo: "#14532d",
    surface: "#f0fdf4",
    accent: "#facc15",
    accentSoft: "#fde047",
    text: "#f7fee7",
    muted: "#dcfce7",
    ink: "#052e16",
  },
};

const STYLE_OPTIONS = {
  wordmark: { label: "Wordmark" },
  shield: { label: "Shield" },
  mountain: { label: "Mountain Seal" },
  beacon: { label: "Emergency Beacon" },
};

function readBootstrap() {
  const script = document.getElementById("ad-simulator-data");
  if (!script) {
    return {
      agencyName: "Your Agency",
      initialImageUrl: "",
      initialView: "banner",
      initialCounty: "Cascade County",
      initialTargetUrl: "",
      publicPreviewBaseUrl: "/advertise/bail-bonds",
      checkoutBaseUrl: "/advertise/bail-bonds/checkout",
      uploadEndpoint: "/api/bail-ads/simulator-upload",
      eventEndpoint: "/api/bail-ads/simulator-event",
      internalMode: false,
      allowInquirySync: false,
    };
  }

  try {
    const payload = JSON.parse(script.textContent || "{}");
    return {
      agencyName: payload.agencyName || "Your Agency",
      initialImageUrl: payload.initialImageUrl || "",
      initialView: payload.initialView === "sidebar" ? "sidebar" : "banner",
      initialCounty: COUNTY_FEEDS[payload.initialCounty] ? payload.initialCounty : "Cascade County",
      initialTargetUrl: payload.initialTargetUrl || "",
      publicPreviewBaseUrl: payload.publicPreviewBaseUrl || "/advertise/bail-bonds",
      checkoutBaseUrl: payload.checkoutBaseUrl || "/advertise/bail-bonds/checkout",
      uploadEndpoint: payload.uploadEndpoint || "/api/bail-ads/simulator-upload",
      eventEndpoint: payload.eventEndpoint || "/api/bail-ads/simulator-event",
      internalMode: Boolean(payload.internalMode),
      allowInquirySync: Boolean(payload.allowInquirySync),
    };
  } catch {
    return {
      agencyName: "Your Agency",
      initialImageUrl: "",
      initialView: "banner",
      initialCounty: "Cascade County",
      initialTargetUrl: "",
      publicPreviewBaseUrl: "/advertise/bail-bonds",
      checkoutBaseUrl: "/advertise/bail-bonds/checkout",
      uploadEndpoint: "/api/bail-ads/simulator-upload",
      eventEndpoint: "/api/bail-ads/simulator-event",
      internalMode: false,
      allowInquirySync: false,
    };
  }
}

function escapeXml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function truncateText(value, maxLength) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
}

function initialsFromName(name) {
  const tokens = String(name || "")
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .slice(0, 3);
  const initials = tokens.map((token) => token[0]).join("").toUpperCase();
  return initials || "MB";
}

function slugifyFilename(value) {
  return String(value || "simulator-preview")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "simulator-preview";
}

function placementDimensions(view, isMobile) {
  if (view === "sidebar") {
    return isMobile
      ? { width: 320, height: 240, label: "Compressed mobile sidebar pitch" }
      : { width: 300, height: 600, label: "300x600 desktop sidebar" };
  }
  return isMobile
    ? { width: 320, height: 50, label: "320x50 mobile header" }
    : { width: 970, height: 250, label: "970x250 desktop banner" };
}

function generatorLayout({
  width,
  height,
  stylePreset,
  theme,
  title,
  tagline,
  phoneNumber,
  ctaLabel,
  badgeText,
  legalLine,
  initials,
}) {
  const isSidebar = height > width;
  const titleSize = isSidebar ? 32 : 42;
  const taglineSize = isSidebar ? 15 : 18;
  const phoneSize = isSidebar ? 19 : 20;
  const badgeWidth = Math.min(width * 0.34, 180);
  const ctaWidth = Math.min(width * 0.34, 170);
  const leftPad = isSidebar ? 26 : 34;
  const iconBox = isSidebar ? 80 : 96;

  let iconMarkup = "";
  if (stylePreset === "shield") {
    iconMarkup = `
      <path d="M${leftPad + 24} 44 L${leftPad + iconBox - 24} 44 L${leftPad + iconBox - 18} 82
               C${leftPad + iconBox - 20} 116, ${leftPad + 70} 148, ${leftPad + iconBox / 2} 164
               C${leftPad + 26} 148, ${leftPad + 4} 116, ${leftPad + 6} 82 Z"
            fill="${theme.accent}" opacity="0.96" />
      <text x="${leftPad + iconBox / 2}" y="112" text-anchor="middle"
            font-family="Arial, sans-serif" font-size="${isSidebar ? 30 : 34}" font-weight="800"
            fill="${theme.ink}">${escapeXml(initials)}</text>
    `;
  } else if (stylePreset === "mountain") {
    iconMarkup = `
      <circle cx="${leftPad + iconBox / 2}" cy="102" r="${isSidebar ? 42 : 46}" fill="${theme.surface}" opacity="0.16" />
      <path d="M${leftPad + 12} 136 L${leftPad + 42} 84 L${leftPad + 62} 108 L${leftPad + 88} 66 L${leftPad + 118} 136 Z"
            fill="${theme.accent}" />
      <path d="M${leftPad + 34} 136 L${leftPad + 54} 102 L${leftPad + 74} 124 L${leftPad + 98} 90 L${leftPad + 120} 136 Z"
            fill="${theme.accentSoft}" opacity="0.86" />
      <text x="${leftPad + iconBox / 2}" y="172" text-anchor="middle"
            font-family="Arial, sans-serif" font-size="${isSidebar ? 16 : 18}" font-weight="700"
            fill="${theme.muted}">${escapeXml(initials)}</text>
    `;
  } else if (stylePreset === "beacon") {
    iconMarkup = `
      <rect x="${leftPad + 42}" y="54" width="${isSidebar ? 26 : 30}" height="${isSidebar ? 72 : 80}" rx="12" fill="${theme.accent}" />
      <circle cx="${leftPad + 55}" cy="46" r="18" fill="${theme.accentSoft}" />
      <path d="M${leftPad + 20} 46 Q${leftPad - 2} 74 ${leftPad + 18} 102" stroke="${theme.accentSoft}" stroke-width="10" fill="none" stroke-linecap="round" opacity="0.78" />
      <path d="M${leftPad + 90} 46 Q${leftPad + 112} 74 ${leftPad + 92} 102" stroke="${theme.accentSoft}" stroke-width="10" fill="none" stroke-linecap="round" opacity="0.78" />
      <text x="${leftPad + iconBox / 2}" y="168" text-anchor="middle"
            font-family="Arial, sans-serif" font-size="${isSidebar ? 16 : 18}" font-weight="700"
            fill="${theme.muted}">${escapeXml(initials)}</text>
    `;
  } else {
    iconMarkup = `
      <circle cx="${leftPad + iconBox / 2}" cy="102" r="${isSidebar ? 38 : 44}" fill="${theme.accent}" />
      <text x="${leftPad + iconBox / 2}" y="114" text-anchor="middle"
            font-family="Arial, sans-serif" font-size="${isSidebar ? 28 : 32}" font-weight="800"
            fill="${theme.ink}">${escapeXml(initials)}</text>
      <rect x="${leftPad + 14}" y="154" width="${iconBox - 28}" height="4" rx="2" fill="${theme.accentSoft}" opacity="0.86" />
    `;
  }

  if (isSidebar) {
    return `
      ${iconMarkup}
      <g transform="translate(${leftPad}, 208)">
        <rect x="0" y="0" width="${badgeWidth}" height="34" rx="17" fill="${theme.accent}" />
        <text x="${badgeWidth / 2}" y="22" text-anchor="middle" font-family="Arial, sans-serif" font-size="15" font-weight="800" fill="${theme.ink}">
          ${escapeXml(badgeText)}
        </text>
      </g>
      <text x="${leftPad}" y="286" font-family="Arial, sans-serif" font-size="${titleSize}" font-weight="800" fill="${theme.text}">
        ${escapeXml(title)}
      </text>
      <text x="${leftPad}" y="320" font-family="Arial, sans-serif" font-size="${taglineSize}" font-weight="600" fill="${theme.muted}">
        ${escapeXml(tagline)}
      </text>
      <rect x="${leftPad}" y="360" width="${width - leftPad * 2}" height="120" rx="24" fill="${theme.surface}" opacity="0.12" />
      <text x="${leftPad + 20}" y="408" font-family="Arial, sans-serif" font-size="${phoneSize}" font-weight="800" fill="${theme.text}">
        ${escapeXml(phoneNumber)}
      </text>
      <text x="${leftPad + 20}" y="438" font-family="Arial, sans-serif" font-size="15" font-weight="600" fill="${theme.muted}">
        Fast jail release coordination across Montana
      </text>
      <g transform="translate(${leftPad}, 504)">
        <rect x="0" y="0" width="${ctaWidth}" height="50" rx="25" fill="${theme.accent}" />
        <text x="${ctaWidth / 2}" y="31" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="800" fill="${theme.ink}">
          ${escapeXml(ctaLabel)}
        </text>
      </g>
      <text x="${leftPad}" y="${height - 26}" font-family="Arial, sans-serif" font-size="13" font-weight="700" fill="${theme.muted}">
        ${escapeXml(legalLine)}
      </text>
    `;
  }

  return `
    ${iconMarkup}
    <g transform="translate(${width - badgeWidth - 34}, 28)">
      <rect x="0" y="0" width="${badgeWidth}" height="34" rx="17" fill="${theme.accent}" />
      <text x="${badgeWidth / 2}" y="22" text-anchor="middle" font-family="Arial, sans-serif" font-size="15" font-weight="800" fill="${theme.ink}">
        ${escapeXml(badgeText)}
      </text>
    </g>
    <text x="${leftPad + iconBox + 28}" y="102" font-family="Arial, sans-serif" font-size="${titleSize}" font-weight="800" fill="${theme.text}">
      ${escapeXml(title)}
    </text>
    <text x="${leftPad + iconBox + 28}" y="132" font-family="Arial, sans-serif" font-size="${taglineSize}" font-weight="600" fill="${theme.muted}">
      ${escapeXml(tagline)}
    </text>
    <text x="${leftPad + iconBox + 28}" y="182" font-family="Arial, sans-serif" font-size="${phoneSize}" font-weight="800" fill="${theme.text}">
      ${escapeXml(phoneNumber)}
    </text>
    <g transform="translate(${width - ctaWidth - 34}, 156)">
      <rect x="0" y="0" width="${ctaWidth}" height="50" rx="25" fill="${theme.accent}" />
      <text x="${ctaWidth / 2}" y="31" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="800" fill="${theme.ink}">
        ${escapeXml(ctaLabel)}
      </text>
    </g>
    <text x="${leftPad + iconBox + 28}" y="${height - 24}" font-family="Arial, sans-serif" font-size="13" font-weight="700" fill="${theme.muted}">
      ${escapeXml(legalLine)}
    </text>
  `;
}

function buildGeneratedAsset({
  activeView,
  isMobile,
  agencyName,
  tagline,
  phoneNumber,
  ctaLabel,
  badgeText,
  legalLine,
  themePreset,
  stylePreset,
}) {
  const dimensions = placementDimensions(activeView, isMobile);
  const width = dimensions.width;
  const height = dimensions.height;
  const theme = THEME_OPTIONS[themePreset] || THEME_OPTIONS.legal_navy;
  const title = truncateText(agencyName || "Your Agency", activeView === "sidebar" ? 18 : 28);
  const subtitle = truncateText(tagline || "Fast jail release support across Montana", activeView === "sidebar" ? 34 : 52);
  const phone = truncateText(phoneNumber || "Call 24/7", 24);
  const callToAction = truncateText(ctaLabel || "Call Now", 16);
  const badge = truncateText(badgeText || "24/7 Release", 18);
  const legal = truncateText(legalLine || "Montana licensed bail bond service", 38);
  const initials = initialsFromName(title);

  const svgMarkup = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <defs>
        <linearGradient id="sim-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="${theme.bgFrom}" />
          <stop offset="100%" stop-color="${theme.bgTo}" />
        </linearGradient>
        <linearGradient id="sim-fade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="${theme.accent}" stop-opacity="0.14" />
          <stop offset="100%" stop-color="${theme.accentSoft}" stop-opacity="0" />
        </linearGradient>
      </defs>
      <rect width="${width}" height="${height}" rx="${activeView === "sidebar" ? 28 : 24}" fill="url(#sim-bg)" />
      <rect x="0" y="0" width="${width}" height="${height}" rx="${activeView === "sidebar" ? 28 : 24}" fill="url(#sim-fade)" />
      <circle cx="${width - 48}" cy="${activeView === "sidebar" ? 72 : 54}" r="${activeView === "sidebar" ? 74 : 60}" fill="${theme.accent}" opacity="0.08" />
      <circle cx="${width - 22}" cy="${height - 22}" r="${activeView === "sidebar" ? 84 : 54}" fill="${theme.surface}" opacity="0.06" />
      ${generatorLayout({
        width,
        height,
        stylePreset,
        theme,
        title,
        tagline: subtitle,
        phoneNumber: phone,
        ctaLabel: callToAction,
        badgeText: badge,
        legalLine: legal,
        initials,
      })}
    </svg>
  `.trim();

  return {
    ...dimensions,
    svgMarkup,
    dataUrl: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgMarkup)}`,
  };
}

function UploadPlaceholder({ label, dimensions }) {
  return html`
    <div className="flex h-full w-full flex-col items-center justify-center rounded-[24px] border-2 border-dashed border-slate-300 bg-slate-50 px-4 text-center">
      <p className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-400">Your Ad Here</p>
      <p className="mt-2 text-sm font-semibold text-slate-600">${label}</p>
      <p className="mt-1 text-xs text-slate-400">${dimensions}</p>
    </div>
  `;
}

function MockArrestCard({ item, county }) {
  return html`
    <article className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-black text-slate-950">${item.name}</p>
          <p className="mt-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">${county}</p>
        </div>
        <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.14em] text-amber-800">
          Live Feed
        </span>
      </div>
      <p className="mt-4 text-sm text-slate-600">${item.charge}</p>
      <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
        <span>Montana Blotter Arrest Feed</span>
        <span>${item.time}</span>
      </div>
    </article>
  `;
}

function PreviewAdSlot({ src, alt, label, dimensions, className, imageClassName }) {
  return html`
    <div className=${className}>
      ${src
        ? html`<img src=${src} alt=${alt} className=${imageClassName} />`
        : html`<${UploadPlaceholder} label=${label} dimensions=${dimensions} />`}
    </div>
  `;
}

function AdSimulator(bootstrap) {
  const inputRef = useRef(null);
  const messageTimerRef = useRef(null);
  const initialTracked = useRef({
    view: bootstrap.initialView,
    county: bootstrap.initialCounty,
    mobile: false,
  });
  const [activeView, setActiveView] = useState(bootstrap.initialView);
  const [activeCounty, setActiveCounty] = useState(bootstrap.initialCounty);
  const [isMobile, setIsMobile] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [agencyName, setAgencyName] = useState(bootstrap.agencyName || "Your Agency");
  const [targetUrl, setTargetUrl] = useState(bootstrap.initialTargetUrl || "");
  const [imageUrl, setImageUrl] = useState(bootstrap.initialImageUrl || "");
  const [shareableAssetUrl, setShareableAssetUrl] = useState(bootstrap.initialImageUrl || "");
  const [imageFile, setImageFile] = useState(null);
  const [objectUrl, setObjectUrl] = useState("");
  const [statusText, setStatusText] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [creativeMode, setCreativeMode] = useState(bootstrap.initialImageUrl ? "upload" : "generated");
  const [tagline, setTagline] = useState("Fast jail release support across Montana");
  const [phoneNumber, setPhoneNumber] = useState("Call 24/7");
  const [ctaLabel, setCtaLabel] = useState("Call Now");
  const [badgeText, setBadgeText] = useState("24/7 Release");
  const [legalLine, setLegalLine] = useState("Montana licensed bail bond service");
  const [themePreset, setThemePreset] = useState("legal_navy");
  const [stylePreset, setStylePreset] = useState("shield");

  useEffect(() => {
    trackEvent("page_view");
    return () => {
      if (messageTimerRef.current) {
        window.clearTimeout(messageTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    return () => {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [objectUrl]);

  useEffect(() => {
    if (initialTracked.current.view !== activeView) {
      trackEvent("view_switch");
      initialTracked.current.view = activeView;
    }
  }, [activeView]);

  useEffect(() => {
    if (initialTracked.current.county !== activeCounty) {
      trackEvent("county_switch");
      initialTracked.current.county = activeCounty;
    }
  }, [activeCounty]);

  useEffect(() => {
    if (initialTracked.current.mobile !== isMobile) {
      trackEvent("mobile_toggle");
      initialTracked.current.mobile = isMobile;
    }
  }, [isMobile]);

  const packageDetails = useMemo(
    () => PACKAGE_DETAILS[activeView] || PACKAGE_DETAILS.banner,
    [activeView]
  );
  const arrests = COUNTY_FEEDS[activeCounty] || COUNTY_FEEDS["Cascade County"];
  const previewTitle = activeView === "banner" ? "Top Banner View" : "Sticky Sidebar View";
  const deviceFrameClass = isMobile
    ? "mx-auto w-full max-w-[390px] rounded-[32px] border border-slate-300 bg-slate-200 p-3 shadow-2xl"
    : "w-full rounded-[32px] border border-slate-200 bg-slate-100 p-4 shadow-2xl";
  const generatedAsset = useMemo(
    () =>
      buildGeneratedAsset({
        activeView,
        isMobile,
        agencyName,
        tagline,
        phoneNumber,
        ctaLabel,
        badgeText,
        legalLine,
        themePreset,
        stylePreset,
      }),
    [activeView, isMobile, agencyName, tagline, phoneNumber, ctaLabel, badgeText, legalLine, themePreset, stylePreset]
  );
  const previewSrc = creativeMode === "generated" ? generatedAsset.dataUrl : imageUrl;
  const sourceLabel = creativeMode === "generated" ? "Built-In Generator" : "Uploaded Logo";

  function setMessage(text) {
    setStatusText(text || "");
    if (text) {
      if (messageTimerRef.current) {
        window.clearTimeout(messageTimerRef.current);
      }
      messageTimerRef.current = window.setTimeout(() => setStatusText(""), 3200);
    }
  }

  function sendJson(url, payload) {
    try {
      const body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        const blob = new Blob([body], { type: "application/json" });
        navigator.sendBeacon(url, blob);
        return;
      }
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        keepalive: true,
      }).catch(() => {});
    } catch {}
  }

  function trackEvent(eventType, extra = {}) {
    if (!bootstrap.eventEndpoint) return;
    sendJson(bootstrap.eventEndpoint, {
      event_type: eventType,
      source: bootstrap.internalMode ? "admin_simulator" : "ad_simulator",
      sim_view: activeView,
      county: activeCounty,
      agency_name: agencyName,
      asset_path: shareableAssetUrl || "",
      share_url: extra.shareUrl || "",
      internal_mode: bootstrap.internalMode,
    });
  }

  function updatePreview(file) {
    if (!file || !file.type.startsWith("image/")) return;

    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
    }

    const nextObjectUrl = URL.createObjectURL(file);
    setObjectUrl(nextObjectUrl);
    setImageUrl(nextObjectUrl);
    setShareableAssetUrl("");
    setImageFile(file);
    setCreativeMode("upload");
  }

  function handleFileChange(event) {
    const file = event.target.files?.[0];
    if (file) updatePreview(file);
  }

  function handleDrop(event) {
    event.preventDefault();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) updatePreview(file);
  }

  function clearImage() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      setObjectUrl("");
    }
    setImageUrl("");
    setShareableAssetUrl("");
    setImageFile(null);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
    setMessage("Upload cleared. Switch to Built-In Generator to keep mocking creatives.");
  }

  function svgMarkupToDataUrl(svgMarkup) {
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgMarkup)}`;
  }

  async function generatedPngFile() {
    const source = svgMarkupToDataUrl(generatedAsset.svgMarkup);
    return new Promise((resolve, reject) => {
      const img = new window.Image();
      img.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = generatedAsset.width;
          canvas.height = generatedAsset.height;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, generatedAsset.width, generatedAsset.height);
          canvas.toBlob(
            (blob) => {
              if (!blob) {
                reject(new Error("Unable to export generated creative."));
                return;
              }
              const filename = `${slugifyFilename(agencyName)}-${activeView}-preview.png`;
              resolve(new File([blob], filename, { type: "image/png" }));
            },
            "image/png",
            0.96
          );
        } catch (error) {
          reject(error);
        }
      };
      img.onerror = () => reject(new Error("Unable to render generated creative."));
      img.src = source;
    });
  }

  async function ensureShareableAsset() {
    if (shareableAssetUrl) return shareableAssetUrl;
    if (!bootstrap.uploadEndpoint) return "";

    let fileToUpload = null;
    if (creativeMode === "generated") {
      try {
        fileToUpload = await generatedPngFile();
      } catch (error) {
        setMessage(error.message || "Unable to export generated creative.");
        return "";
      }
    } else if (imageFile) {
      fileToUpload = imageFile;
    }

    if (!fileToUpload) return "";

    const data = new FormData();
    data.append("file", fileToUpload);
    data.append("agency_name", agencyName);
    data.append("sim_view", activeView);
    data.append("county", activeCounty);
    data.append("source", bootstrap.internalMode ? "admin_simulator" : "ad_simulator");
    data.append("internal_mode", bootstrap.internalMode ? "1" : "0");

    setIsUploading(true);
    try {
      const response = await fetch(bootstrap.uploadEndpoint, {
        method: "POST",
        body: data,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.asset_url) {
        throw new Error(payload.error || "Upload failed");
      }
      setShareableAssetUrl(payload.asset_url);
      setMessage(creativeMode === "generated" ? "Generated creative exported and uploaded." : "Preview asset uploaded.");
      return payload.asset_url;
    } catch (error) {
      setMessage(error.message || "Unable to upload preview asset.");
      return "";
    } finally {
      setIsUploading(false);
    }
  }

  function buildPreviewUrl(assetUrl = shareableAssetUrl) {
    const params = new URLSearchParams();
    if (agencyName.trim()) params.set("agency_name", agencyName.trim());
    if (assetUrl) params.set("logo_url", assetUrl);
    if (activeView) params.set("sim_view", activeView);
    if (activeCounty) params.set("sim_county", activeCounty);
    if (targetUrl.trim()) params.set("target_url", targetUrl.trim());
    return `${bootstrap.publicPreviewBaseUrl}?${params.toString()}`;
  }

  async function copyShareLink() {
    const assetUrl = await ensureShareableAsset();
    const shareUrl = buildPreviewUrl(assetUrl);
    const absoluteShareUrl = `${window.location.origin}${shareUrl}`;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(absoluteShareUrl);
      } else {
        const input = document.createElement("input");
        input.value = absoluteShareUrl;
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        document.body.removeChild(input);
      }
      trackEvent("share_link", { shareUrl: absoluteShareUrl });
      setMessage("Share link copied.");
    } catch {
      setMessage("Unable to copy link.");
    }
  }

  async function openPublicPreview() {
    const assetUrl = await ensureShareableAsset();
    const shareUrl = buildPreviewUrl(assetUrl);
    const absoluteShareUrl = `${window.location.origin}${shareUrl}`;
    trackEvent("public_preview_open", { shareUrl: absoluteShareUrl });
    window.open(shareUrl, "_blank", "noopener,noreferrer");
  }

  async function syncInquiryForm() {
    const form = document.getElementById("bail-ad-inquiry-form");
    if (!form) {
      await openPublicPreview();
      return;
    }
    const assetUrl = await ensureShareableAsset();
    const shareUrl = buildPreviewUrl(assetUrl);
    const absoluteShareUrl = `${window.location.origin}${shareUrl}`;
    const packageId = packageDetails.packageId;
    const businessInput = form.querySelector('input[name="business_name"]');
    const websiteInput = form.querySelector('input[name="website_url"]');
    const messageInput = form.querySelector('textarea[name="message"]');
    const packageInput = form.querySelector('select[name="package_interest"]');
    const sourceInput = form.querySelector('input[name="source"]');
    const logoInput = form.querySelector('input[name="simulator_logo_path"]');
    const targetInput = form.querySelector('input[name="simulator_target_url"]');
    const shareInput = form.querySelector('input[name="simulator_share_url"]');
    const viewInput = form.querySelector('input[name="simulator_view"]');

    if (businessInput && agencyName.trim() && (!businessInput.value || businessInput.value === "Your Agency")) {
      businessInput.value = agencyName.trim();
    }
    if (websiteInput && targetUrl.trim() && !websiteInput.value) {
      websiteInput.value = targetUrl.trim();
    }
    if (messageInput && !messageInput.value.trim()) {
      messageInput.value = `Simulator creative: ${sourceLabel}. Theme: ${(THEME_OPTIONS[themePreset] || THEME_OPTIONS.legal_navy).label}. Style: ${(STYLE_OPTIONS[stylePreset] || STYLE_OPTIONS.shield).label}. CTA: ${ctaLabel}.`;
    }
    if (packageInput) packageInput.value = packageId;
    if (sourceInput) sourceInput.value = bootstrap.internalMode ? "admin_simulator_inquiry" : "simulator_inquiry";
    if (logoInput) logoInput.value = assetUrl || "";
    if (targetInput) targetInput.value = targetUrl.trim();
    if (shareInput) shareInput.value = absoluteShareUrl;
    if (viewInput) viewInput.value = activeView;

    trackEvent("inquiry_sync", { shareUrl: absoluteShareUrl });
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    setMessage("Inquiry form synced.");
  }

  async function continueToCheckout() {
    const assetUrl = await ensureShareableAsset();
    const shareUrl = buildPreviewUrl(assetUrl);
    const absoluteShareUrl = `${window.location.origin}${shareUrl}`;
    const params = new URLSearchParams();
    params.set("package", packageDetails.packageId);
    params.set("source", bootstrap.internalMode ? "admin_simulator_checkout" : "simulator_checkout");
    if (agencyName.trim()) params.set("agency_name", agencyName.trim());
    if (targetUrl.trim()) params.set("target_url", targetUrl.trim());
    if (assetUrl) params.set("simulator_logo_path", assetUrl);
    if (targetUrl.trim()) params.set("simulator_target_url", targetUrl.trim());
    params.set("simulator_share_url", absoluteShareUrl);
    params.set("simulator_view", activeView);
    trackEvent("checkout_click", { shareUrl: absoluteShareUrl });
    window.location.href = `${bootstrap.checkoutBaseUrl}?${params.toString()}`;
  }

  const primaryInquiryLabel = bootstrap.allowInquirySync ? "Use in Inquiry Form" : "Open Public Preview";
  const themeLabel = (THEME_OPTIONS[themePreset] || THEME_OPTIONS.legal_navy).label;
  const styleLabel = (STYLE_OPTIONS[stylePreset] || STYLE_OPTIONS.shield).label;
  const uploadNotice =
    creativeMode === "generated"
      ? "Generated mockups are exported to PNG automatically for share links and checkout."
      : "Uploaded logos can still be combined with the simulator's county and device previews.";

  return html`
    <section className="rounded-[32px] border border-slate-200 bg-white shadow-xl">
      <div className="border-b border-slate-200 bg-slate-950 px-6 py-6 text-white sm:px-8">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-[11px] font-black uppercase tracking-[0.22em] text-amber-300">
              ${bootstrap.internalMode ? "Internal Sales Simulator" : "Ad Preview Simulator"}
            </p>
            <h2 className="mt-3 text-3xl font-black tracking-tight">
              Build a full bail bonds creative, not just a blank logo placeholder
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
              Upload a logo or generate a branded mock ad, switch placements, preview counties, and hand the creative straight into share links or checkout.
            </p>
          </div>

          <div className="inline-flex rounded-full border border-slate-700 bg-slate-900 p-1">
            <button
              type="button"
              onClick=${() => setActiveView("banner")}
              className=${`rounded-full px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition ${
                activeView === "banner" ? "bg-amber-500 text-slate-950" : "text-slate-300 hover:text-white"
              }`}
            >
              Top Banner
            </button>
            <button
              type="button"
              onClick=${() => setActiveView("sidebar")}
              className=${`rounded-full px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition ${
                activeView === "sidebar" ? "bg-amber-500 text-slate-950" : "text-slate-300 hover:text-white"
              }`}
            >
              Sticky Sidebar
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-6 px-6 py-6 lg:grid-cols-[380px_minmax(0,1fr)] lg:px-8 lg:py-8">
        <aside className="rounded-[28px] border border-slate-200 bg-slate-50 p-5">
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-500">Campaign Setup</p>

          <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
            Agency Name
            <input
              type="text"
              value=${agencyName}
              onInput=${(event) => setAgencyName(event.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
            />
          </label>

          <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
            Landing URL
            <input
              type="url"
              value=${targetUrl}
              onInput=${(event) => setTargetUrl(event.target.value)}
              placeholder="https://youragency.com"
              className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
            />
          </label>

          <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
            County Feed Preview
            <select
              value=${activeCounty}
              onChange=${(event) => setActiveCounty(event.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
            >
              ${Object.keys(COUNTY_FEEDS).map(
                (countyName) => html`<option key=${countyName} value=${countyName}>${countyName}</option>`
              )}
            </select>
          </label>

          <div className="mt-5 rounded-[24px] border border-slate-200 bg-white p-4">
            <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Creative Source</p>
            <div className="mt-3 inline-flex rounded-full border border-slate-200 bg-slate-50 p-1">
              <button
                type="button"
                onClick=${() => setCreativeMode("generated")}
                className=${`rounded-full px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition ${
                  creativeMode === "generated" ? "bg-slate-950 text-white" : "text-slate-500 hover:text-slate-900"
                }`}
              >
                Built-In Generator
              </button>
              <button
                type="button"
                onClick=${() => setCreativeMode("upload")}
                className=${`rounded-full px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition ${
                  creativeMode === "upload" ? "bg-slate-950 text-white" : "text-slate-500 hover:text-slate-900"
                }`}
              >
                Upload Logo
              </button>
            </div>
            <p className="mt-3 text-xs text-slate-500">${uploadNotice}</p>
          </div>

          ${creativeMode === "generated"
            ? html`
                <div className="mt-5 rounded-[24px] border border-slate-200 bg-white p-4">
                  <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Generator Options</p>

                  <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                    Tagline
                    <input
                      type="text"
                      value=${tagline}
                      onInput=${(event) => setTagline(event.target.value)}
                      className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                    />
                  </label>

                  <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <label className="block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                      Phone Line
                      <input
                        type="text"
                        value=${phoneNumber}
                        onInput=${(event) => setPhoneNumber(event.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                      />
                    </label>
                    <label className="block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                      CTA Text
                      <input
                        type="text"
                        value=${ctaLabel}
                        onInput=${(event) => setCtaLabel(event.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                      />
                    </label>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <label className="block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                      Badge
                      <input
                        type="text"
                        value=${badgeText}
                        onInput=${(event) => setBadgeText(event.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                      />
                    </label>
                    <label className="block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                      Legal Line
                      <input
                        type="text"
                        value=${legalLine}
                        onInput=${(event) => setLegalLine(event.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                      />
                    </label>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <label className="block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                      Theme
                      <select
                        value=${themePreset}
                        onChange=${(event) => setThemePreset(event.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                      >
                        ${Object.entries(THEME_OPTIONS).map(
                          ([key, option]) => html`<option key=${key} value=${key}>${option.label}</option>`
                        )}
                      </select>
                    </label>
                    <label className="block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
                      Logo Style
                      <select
                        value=${stylePreset}
                        onChange=${(event) => setStylePreset(event.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
                      >
                        ${Object.entries(STYLE_OPTIONS).map(
                          ([key, option]) => html`<option key=${key} value=${key}>${option.label}</option>`
                        )}
                      </select>
                    </label>
                  </div>
                </div>
              `
            : html`
                <div
                  onDragOver=${(event) => {
                    event.preventDefault();
                    setIsDragging(true);
                  }}
                  onDragLeave=${() => setIsDragging(false)}
                  onDrop=${handleDrop}
                  className=${`mt-5 rounded-[24px] border-2 border-dashed px-5 py-8 text-center transition ${
                    isDragging ? "border-amber-400 bg-amber-50" : "border-slate-300 bg-white"
                  }`}
                >
                  <input
                    ref=${inputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/jpg,image/webp,image/gif"
                    className="hidden"
                    onChange=${handleFileChange}
                  />

                  <p className="text-sm font-semibold text-slate-700">
                    ${imageUrl ? "Logo loaded into preview." : "Drop your logo here"}
                  </p>
                  <p className="mt-2 text-xs text-slate-400">
                    PNG, JPG, WEBP, or GIF. The uploaded asset stays reusable for share links and checkout.
                  </p>

                  <div className="mt-5 flex flex-wrap justify-center gap-3">
                    <button
                      type="button"
                      onClick=${() => inputRef.current?.click()}
                      className="inline-flex items-center justify-center rounded-full bg-slate-950 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800"
                    >
                      Choose File
                    </button>
                    ${imageUrl
                      ? html`
                          <button
                            type="button"
                            onClick=${clearImage}
                            className="inline-flex items-center justify-center rounded-full border border-slate-300 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-400 hover:bg-slate-100"
                          >
                            Clear
                          </button>
                        `
                      : null}
                  </div>
                </div>
              `}

          <div className="mt-6 rounded-[24px] border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Device Preview</p>
                <p className="mt-1 text-sm font-semibold text-slate-900">${isMobile ? "Mobile Screen" : "Desktop Feed"}</p>
              </div>
              <button
                type="button"
                onClick=${() => setIsMobile((current) => !current)}
                className=${`relative inline-flex h-7 w-14 items-center rounded-full transition ${
                  isMobile ? "bg-amber-500" : "bg-slate-300"
                }`}
                aria-pressed=${String(isMobile)}
              >
                <span className=${`inline-block h-5 w-5 rounded-full bg-white shadow transition ${isMobile ? "translate-x-8" : "translate-x-1"}`}></span>
              </button>
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Mobile mode shows the header in a 320x50 unit. Sidebar mode collapses into a handset-style pitch deck preview.
            </p>
          </div>

          <div className="mt-6 rounded-[24px] border border-amber-200 bg-amber-50 p-4">
            <p className="text-[11px] font-black uppercase tracking-[0.18em] text-amber-700">Selected Package</p>
            <h4 className="mt-2 text-lg font-black text-slate-950">${packageDetails.label}</h4>
            <p className="mt-2 text-sm text-slate-600">${packageDetails.summary}</p>
            <div className="mt-4 flex flex-wrap gap-3">
              <span className="rounded-full bg-white px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] text-slate-900">
                ${packageDetails.monthly}
              </span>
              <span className="rounded-full border border-amber-200 bg-white px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] text-slate-500">
                ${packageDetails.annual}
              </span>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="rounded-full border border-amber-200 bg-white px-3 py-1.5 text-[10px] font-black uppercase tracking-[0.16em] text-slate-600">
                ${sourceLabel}
              </span>
              ${creativeMode === "generated"
                ? html`
                    <span className="rounded-full border border-amber-200 bg-white px-3 py-1.5 text-[10px] font-black uppercase tracking-[0.16em] text-slate-600">
                      ${themeLabel}
                    </span>
                    <span className="rounded-full border border-amber-200 bg-white px-3 py-1.5 text-[10px] font-black uppercase tracking-[0.16em] text-slate-600">
                      ${styleLabel}
                    </span>
                  `
                : null}
            </div>
            <p className="mt-3 text-xs text-slate-500">${packageDetails.dimensions}</p>
          </div>
        </aside>

        <div>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Live Preview</p>
              <h3 className="mt-1 text-2xl font-black text-slate-950">${previewTitle}</h3>
              <p className="mt-1 text-sm text-slate-500">${activeCounty} arrest feed mockup</p>
            </div>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] text-slate-500">
              ${isMobile ? "Mobile" : "Desktop"}
            </span>
          </div>

          <div className=${deviceFrameClass}>
            <div className="overflow-hidden rounded-[24px] border border-slate-300 bg-white">
              <div className="border-b border-slate-200 bg-slate-950 px-4 py-4 text-white">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-lg font-black tracking-tight">Montana Blotter</p>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-300">Daily Public Safety Dispatch</p>
                  </div>
                  <div className="hidden text-right md:block">
                    <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Previewing For</p>
                    <p className="mt-1 text-sm font-semibold text-slate-100">${agencyName || "Your Agency"}</p>
                  </div>
                </div>
              </div>

              <div className="border-b border-slate-200 bg-white px-4 py-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">County Feed</p>
                    <p className="mt-1 text-sm font-semibold text-slate-900">${activeCounty}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Creative</p>
                    <p className="mt-1 text-sm font-semibold text-slate-900">${sourceLabel}</p>
                  </div>
                </div>
              </div>

              ${activeView === "banner"
                ? html`
                    <div className="border-b border-slate-200 bg-slate-100 px-4 py-4">
                      <${PreviewAdSlot}
                        src=${previewSrc}
                        alt=${`${agencyName} top banner preview`}
                        label="Top Banner Placement"
                        dimensions=${isMobile ? "320x50 mobile header" : "970x250 desktop banner"}
                        className=${`mx-auto overflow-hidden rounded-[24px] border border-slate-300 bg-white ${
                          isMobile ? "h-[50px] w-[320px] max-w-full" : "h-[250px] w-full max-w-[970px]"
                        }`}
                        imageClassName=${`h-full w-full ${creativeMode === "generated" ? "object-cover" : `object-contain bg-white ${isMobile ? "p-2" : "p-4"}`}`}
                      />
                    </div>
                  `
                : null}

              <div
                className=${`gap-4 bg-slate-100 p-4 ${
                  activeView === "sidebar" && !isMobile ? "grid lg:grid-cols-[minmax(0,1fr)_300px]" : "grid grid-cols-1"
                }`}
              >
                <div className="space-y-4">
                  ${arrests.map(
                    (item) => html`<${MockArrestCard} key=${`${item.name}-${item.time}`} item=${item} county=${activeCounty} />`
                  )}
                </div>

                ${activeView === "sidebar"
                  ? html`
                      <div className=${isMobile ? "pt-2" : ""}>
                        <${PreviewAdSlot}
                          src=${previewSrc}
                          alt=${`${agencyName} sidebar preview`}
                          label="Sticky Sidebar Placement"
                          dimensions=${isMobile ? "Compressed mobile sales mockup" : "300x600 desktop sidebar"}
                          className=${`overflow-hidden rounded-[24px] border border-slate-300 bg-white ${
                            isMobile ? "mx-auto h-[240px] w-full max-w-[320px]" : "sticky top-4 h-[600px] w-[300px]"
                          }`}
                          imageClassName=${`h-full w-full ${creativeMode === "generated" ? "object-cover" : "object-contain bg-white p-5"}`}
                        />
                      </div>
                    `
                  : null}
              </div>
            </div>
          </div>

          <div className="mt-6 rounded-[28px] border border-slate-200 bg-slate-50 p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Conversion Actions</p>
                <p className="mt-2 text-sm text-slate-600">
                  Turn this mockup into a form-prefill, share link, or checkout-ready preview.
                </p>
                ${statusText
                  ? html`<p className="mt-2 text-sm font-semibold text-emerald-700">${statusText}</p>`
                  : null}
              </div>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick=${bootstrap.allowInquirySync ? syncInquiryForm : openPublicPreview}
                  className="inline-flex items-center justify-center rounded-full border border-slate-300 bg-white px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-400 hover:bg-slate-100"
                >
                  ${primaryInquiryLabel}
                </button>
                <button
                  type="button"
                  disabled=${isUploading}
                  onClick=${copyShareLink}
                  className="inline-flex items-center justify-center rounded-full border border-amber-300 bg-amber-50 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-amber-800 transition hover:bg-amber-100 disabled:opacity-60"
                >
                  ${isUploading ? "Uploading..." : "Copy Share Link"}
                </button>
                <button
                  type="button"
                  disabled=${isUploading}
                  onClick=${continueToCheckout}
                  className="inline-flex items-center justify-center rounded-full bg-slate-950 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800 disabled:opacity-60"
                >
                  Continue to Checkout
                </button>
              </div>
            </div>
          </div>

          <div className="mt-6 rounded-[28px] border border-slate-200 bg-slate-50 p-5">
            <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Generator Snapshot</p>
            <div className="mt-4 grid gap-4 md:grid-cols-4">
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Placement</p>
                <p className="mt-2 text-lg font-black text-slate-950">${packageDetails.label}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Source</p>
                <p className="mt-2 text-lg font-black text-slate-950">${sourceLabel}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Theme</p>
                <p className="mt-2 text-lg font-black text-slate-950">${themeLabel}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Style</p>
                <p className="mt-2 text-lg font-black text-slate-950">${styleLabel}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  `;
}

const rootNode = document.getElementById("ad-simulator-root");

if (rootNode) {
  const bootstrap = readBootstrap();
  createRoot(rootNode).render(
    html`<${AdSimulator}
      agencyName=${bootstrap.agencyName}
      initialImageUrl=${bootstrap.initialImageUrl}
      initialView=${bootstrap.initialView}
      initialCounty=${bootstrap.initialCounty}
      initialTargetUrl=${bootstrap.initialTargetUrl}
      publicPreviewBaseUrl=${bootstrap.publicPreviewBaseUrl}
      checkoutBaseUrl=${bootstrap.checkoutBaseUrl}
      uploadEndpoint=${bootstrap.uploadEndpoint}
      eventEndpoint=${bootstrap.eventEndpoint}
      internalMode=${bootstrap.internalMode}
      allowInquirySync=${bootstrap.allowInquirySync}
    />`
  );
}
