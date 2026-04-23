import React, { useEffect, useMemo, useRef, useState } from "react";

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

function UploadPlaceholder({ label, dimensions }) {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center rounded-[24px] border-2 border-dashed border-slate-300 bg-slate-50 px-4 text-center">
      <p className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-400">Your Ad Here</p>
      <p className="mt-2 text-sm font-semibold text-slate-600">{label}</p>
      <p className="mt-1 text-xs text-slate-400">{dimensions}</p>
    </div>
  );
}

function MockArrestCard({ item, county }) {
  return (
    <article className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-black text-slate-950">{item.name}</p>
          <p className="mt-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">{county}</p>
        </div>
        <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.14em] text-amber-800">
          Live Feed
        </span>
      </div>
      <p className="mt-4 text-sm text-slate-600">{item.charge}</p>
      <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
        <span>Montana Blotter Arrest Feed</span>
        <span>{item.time}</span>
      </div>
    </article>
  );
}

function PreviewAdSlot({ src, alt, label, dimensions, className, imageClassName }) {
  return (
    <div className={className}>
      {src ? <img src={src} alt={alt} className={imageClassName} /> : <UploadPlaceholder label={label} dimensions={dimensions} />}
    </div>
  );
}

export default function AdSimulator({
  agencyName: initialAgencyName = "Your Agency",
  initialImageUrl = "",
  initialView = "banner",
  initialCounty = "Cascade County",
  initialTargetUrl = "",
  publicPreviewBaseUrl = "/advertise/bail-bonds",
  checkoutBaseUrl = "/advertise/bail-bonds/checkout",
  uploadEndpoint = "/api/bail-ads/simulator-upload",
  eventEndpoint = "/api/bail-ads/simulator-event",
  internalMode = false,
  allowInquirySync = false,
}) {
  const inputRef = useRef(null);
  const messageTimerRef = useRef(null);
  const initialTracked = useRef({
    view: initialView === "sidebar" ? "sidebar" : "banner",
    county: COUNTY_FEEDS[initialCounty] ? initialCounty : "Cascade County",
    mobile: false,
  });
  const [activeView, setActiveView] = useState(initialView === "sidebar" ? "sidebar" : "banner");
  const [activeCounty, setActiveCounty] = useState(COUNTY_FEEDS[initialCounty] ? initialCounty : "Cascade County");
  const [isMobile, setIsMobile] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [agencyName, setAgencyName] = useState(initialAgencyName || "Your Agency");
  const [targetUrl, setTargetUrl] = useState(initialTargetUrl || "");
  const [imageUrl, setImageUrl] = useState(initialImageUrl || "");
  const [shareableAssetUrl, setShareableAssetUrl] = useState(initialImageUrl || "");
  const [imageFile, setImageFile] = useState(null);
  const [objectUrl, setObjectUrl] = useState("");
  const [statusText, setStatusText] = useState("");
  const [isUploading, setIsUploading] = useState(false);

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

  function setMessage(text) {
    setStatusText(text || "");
    if (!text) return;
    if (messageTimerRef.current) {
      window.clearTimeout(messageTimerRef.current);
    }
    messageTimerRef.current = window.setTimeout(() => setStatusText(""), 2800);
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
    } catch {
      return;
    }
  }

  function trackEvent(eventType, extra = {}) {
    if (!eventEndpoint) return;
    sendJson(eventEndpoint, {
      event_type: eventType,
      source: internalMode ? "admin_simulator" : "ad_simulator",
      sim_view: activeView,
      county: activeCounty,
      agency_name: agencyName,
      asset_path: shareableAssetUrl || "",
      share_url: extra.shareUrl || "",
      internal_mode: internalMode,
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
  }

  async function ensureShareableAsset() {
    if (shareableAssetUrl) return shareableAssetUrl;
    if (!imageFile || !uploadEndpoint) return "";

    const data = new FormData();
    data.append("file", imageFile);
    data.append("agency_name", agencyName);
    data.append("sim_view", activeView);
    data.append("county", activeCounty);
    data.append("source", internalMode ? "admin_simulator" : "ad_simulator");
    data.append("internal_mode", internalMode ? "1" : "0");

    setIsUploading(true);
    try {
      const response = await fetch(uploadEndpoint, {
        method: "POST",
        body: data,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.asset_url) {
        throw new Error(payload.error || "Upload failed");
      }
      setShareableAssetUrl(payload.asset_url);
      setMessage("Preview asset uploaded.");
      return payload.asset_url;
    } catch (error) {
      setMessage(error?.message || "Unable to upload preview asset.");
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
    return `${publicPreviewBaseUrl}?${params.toString()}`;
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
    const businessInput = form.querySelector('input[name="business_name"]');
    const websiteInput = form.querySelector('input[name="website_url"]');
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
    if (packageInput) packageInput.value = packageDetails.packageId;
    if (sourceInput) sourceInput.value = internalMode ? "admin_simulator_inquiry" : "simulator_inquiry";
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
    params.set("source", internalMode ? "admin_simulator_checkout" : "simulator_checkout");
    if (agencyName.trim()) params.set("agency_name", agencyName.trim());
    if (targetUrl.trim()) params.set("target_url", targetUrl.trim());
    if (assetUrl) params.set("simulator_logo_path", assetUrl);
    if (targetUrl.trim()) params.set("simulator_target_url", targetUrl.trim());
    params.set("simulator_share_url", absoluteShareUrl);
    params.set("simulator_view", activeView);
    trackEvent("checkout_click", { shareUrl: absoluteShareUrl });
    window.location.href = `${checkoutBaseUrl}?${params.toString()}`;
  }

  const primaryInquiryLabel = allowInquirySync ? "Use in Inquiry Form" : "Open Public Preview";

  return (
    <section className="rounded-[32px] border border-slate-200 bg-white shadow-xl">
      <div className="border-b border-slate-200 bg-slate-950 px-6 py-6 text-white sm:px-8">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-[11px] font-black uppercase tracking-[0.22em] text-amber-300">
              {internalMode ? "Internal Sales Simulator" : "Ad Preview Simulator"}
            </p>
            <h2 className="mt-3 text-3xl font-black tracking-tight">
              Show bondsmen exactly how their logo will sit inside the live feed
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
              Upload a logo, switch placements, preview a county feed, and turn the mockup into a share link or checkout handoff.
            </p>
          </div>

          <div className="inline-flex rounded-full border border-slate-700 bg-slate-900 p-1">
            <button
              type="button"
              onClick={() => setActiveView("banner")}
              className={`rounded-full px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition ${
                activeView === "banner" ? "bg-amber-500 text-slate-950" : "text-slate-300 hover:text-white"
              }`}
            >
              Top Banner
            </button>
            <button
              type="button"
              onClick={() => setActiveView("sidebar")}
              className={`rounded-full px-4 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition ${
                activeView === "sidebar" ? "bg-amber-500 text-slate-950" : "text-slate-300 hover:text-white"
              }`}
            >
              Sticky Sidebar
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-6 px-6 py-6 lg:grid-cols-[360px_minmax(0,1fr)] lg:px-8 lg:py-8">
        <aside className="rounded-[28px] border border-slate-200 bg-slate-50 p-5">
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-500">Campaign Setup</p>

          <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
            Agency Name
            <input
              type="text"
              value={agencyName}
              onInput={(event) => setAgencyName(event.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
            />
          </label>

          <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
            Landing URL
            <input
              type="url"
              value={targetUrl}
              onInput={(event) => setTargetUrl(event.target.value)}
              placeholder="https://youragency.com"
              className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
            />
          </label>

          <label className="mt-4 block text-xs font-black uppercase tracking-[0.12em] text-slate-600">
            County Feed Preview
            <select
              value={activeCounty}
              onChange={(event) => setActiveCounty(event.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800"
            >
              {Object.keys(COUNTY_FEEDS).map((countyName) => (
                <option key={countyName} value={countyName}>
                  {countyName}
                </option>
              ))}
            </select>
          </label>

          <div
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={`mt-5 rounded-[24px] border-2 border-dashed px-5 py-8 text-center transition ${
              isDragging ? "border-amber-400 bg-amber-50" : "border-slate-300 bg-white"
            }`}
          >
            <input
              ref={inputRef}
              type="file"
              accept="image/png,image/jpeg,image/jpg,image/webp,image/gif"
              className="hidden"
              onChange={handleFileChange}
            />

            <p className="text-sm font-semibold text-slate-700">
              {imageUrl ? "Logo loaded into preview." : "Drop your logo here"}
            </p>
            <p className="mt-2 text-xs text-slate-400">
              PNG or JPG. Upload once and the simulator can generate shareable preview links.
            </p>

            <div className="mt-5 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="inline-flex items-center justify-center rounded-full bg-slate-950 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800"
              >
                Choose File
              </button>
              {imageUrl ? (
                <button
                  type="button"
                  onClick={clearImage}
                  className="inline-flex items-center justify-center rounded-full border border-slate-300 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-400 hover:bg-slate-100"
                >
                  Clear
                </button>
              ) : null}
            </div>
          </div>

          <div className="mt-6 rounded-[24px] border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Device Preview</p>
                <p className="mt-1 text-sm font-semibold text-slate-900">{isMobile ? "Mobile Screen" : "Desktop Feed"}</p>
              </div>
              <button
                type="button"
                onClick={() => setIsMobile((current) => !current)}
                className={`relative inline-flex h-7 w-14 items-center rounded-full transition ${
                  isMobile ? "bg-amber-500" : "bg-slate-300"
                }`}
                aria-pressed={String(isMobile)}
              >
                <span
                  className={`inline-block h-5 w-5 rounded-full bg-white shadow transition ${
                    isMobile ? "translate-x-8" : "translate-x-1"
                  }`}
                />
              </button>
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Mobile mode shows the header in a 320x50 unit. Sidebar mode collapses into a handset-style pitch deck preview.
            </p>
          </div>

          <div className="mt-6 rounded-[24px] border border-amber-200 bg-amber-50 p-4">
            <p className="text-[11px] font-black uppercase tracking-[0.18em] text-amber-700">Selected Package</p>
            <h4 className="mt-2 text-lg font-black text-slate-950">{packageDetails.label}</h4>
            <p className="mt-2 text-sm text-slate-600">{packageDetails.summary}</p>
            <div className="mt-4 flex flex-wrap gap-3">
              <span className="rounded-full bg-white px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] text-slate-900">
                {packageDetails.monthly}
              </span>
              <span className="rounded-full border border-amber-200 bg-white px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] text-slate-500">
                {packageDetails.annual}
              </span>
            </div>
            <p className="mt-3 text-xs text-slate-500">{packageDetails.dimensions}</p>
          </div>
        </aside>

        <div>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Live Preview</p>
              <h3 className="mt-1 text-2xl font-black text-slate-950">{previewTitle}</h3>
              <p className="mt-1 text-sm text-slate-500">{activeCounty} arrest feed mockup</p>
            </div>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] text-slate-500">
              {isMobile ? "Mobile" : "Desktop"}
            </span>
          </div>

          <div className={deviceFrameClass}>
            <div className="overflow-hidden rounded-[24px] border border-slate-300 bg-white">
              <div className="border-b border-slate-200 bg-slate-950 px-4 py-4 text-white">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-lg font-black tracking-tight">Montana Blotter</p>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-300">
                      Daily Public Safety Dispatch
                    </p>
                  </div>
                  <div className="hidden text-right md:block">
                    <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Previewing For</p>
                    <p className="mt-1 text-sm font-semibold text-slate-100">{agencyName || "Your Agency"}</p>
                  </div>
                </div>
              </div>

              <div className="border-b border-slate-200 bg-white px-4 py-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">County Feed</p>
                    <p className="mt-1 text-sm font-semibold text-slate-900">{activeCounty}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">Placement</p>
                    <p className="mt-1 text-sm font-semibold text-slate-900">{packageDetails.label}</p>
                  </div>
                </div>
              </div>

              {activeView === "banner" ? (
                <div className="border-b border-slate-200 bg-slate-100 px-4 py-4">
                  <PreviewAdSlot
                    src={imageUrl}
                    alt={`${agencyName} top banner preview`}
                    label="Top Banner Placement"
                    dimensions={isMobile ? "320x50 mobile header" : "970x250 desktop banner"}
                    className={`mx-auto overflow-hidden rounded-[24px] border border-slate-300 bg-white ${
                      isMobile ? "h-[50px] w-[320px] max-w-full" : "h-[250px] w-full max-w-[970px]"
                    }`}
                    imageClassName={`h-full w-full object-contain bg-white ${isMobile ? "p-2" : "p-4"}`}
                  />
                </div>
              ) : null}

              <div
                className={`gap-4 bg-slate-100 p-4 ${
                  activeView === "sidebar" && !isMobile ? "grid lg:grid-cols-[minmax(0,1fr)_300px]" : "grid grid-cols-1"
                }`}
              >
                <div className="space-y-4">
                  {arrests.map((item) => (
                    <MockArrestCard key={`${item.name}-${item.time}`} item={item} county={activeCounty} />
                  ))}
                </div>

                {activeView === "sidebar" ? (
                  <div className={isMobile ? "pt-2" : ""}>
                    <PreviewAdSlot
                      src={imageUrl}
                      alt={`${agencyName} sidebar preview`}
                      label="Sticky Sidebar Placement"
                      dimensions={isMobile ? "Compressed mobile sales mockup" : "300x600 desktop sidebar"}
                      className={`overflow-hidden rounded-[24px] border border-slate-300 bg-white ${
                        isMobile ? "mx-auto h-[240px] w-full max-w-[320px]" : "sticky top-4 h-[600px] w-[300px]"
                      }`}
                      imageClassName="h-full w-full object-contain bg-white p-5"
                    />
                  </div>
                ) : null}
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
                {statusText ? <p className="mt-2 text-sm font-semibold text-emerald-700">{statusText}</p> : null}
              </div>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={allowInquirySync ? syncInquiryForm : openPublicPreview}
                  className="inline-flex items-center justify-center rounded-full border border-slate-300 bg-white px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-400 hover:bg-slate-100"
                >
                  {primaryInquiryLabel}
                </button>
                <button
                  type="button"
                  disabled={isUploading}
                  onClick={copyShareLink}
                  className="inline-flex items-center justify-center rounded-full border border-amber-300 bg-amber-50 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-amber-800 transition hover:bg-amber-100 disabled:opacity-60"
                >
                  {isUploading ? "Uploading..." : "Copy Share Link"}
                </button>
                <button
                  type="button"
                  disabled={isUploading}
                  onClick={continueToCheckout}
                  className="inline-flex items-center justify-center rounded-full bg-slate-950 px-4 py-3 text-[11px] font-black uppercase tracking-[0.18em] text-white transition hover:bg-slate-800 disabled:opacity-60"
                >
                  Continue to Checkout
                </button>
              </div>
            </div>
          </div>

          <div className="mt-6 rounded-[28px] border border-slate-200 bg-slate-50 p-5">
            <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Package Details</p>
            <div className="mt-4 grid gap-4 md:grid-cols-3">
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Placement</p>
                <p className="mt-2 text-lg font-black text-slate-950">{packageDetails.label}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Monthly</p>
                <p className="mt-2 text-lg font-black text-slate-950">{packageDetails.monthly}</p>
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-white p-4">
                <p className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">Annual</p>
                <p className="mt-2 text-lg font-black text-slate-950">{packageDetails.annual}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
