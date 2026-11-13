(() => {
  const $ = (id) => document.getElementById(id);
  const STORE = "kvartyra-lviv-v1";
  const uah = (usd) => (usd && data.usd_rate ? Math.round(usd * data.usd_rate / 1000) * 1000 : null);
  const fmt = (n) => (n == null ? "?" : Math.round(n).toLocaleString("uk-UA"));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  let data = { items: [], complexes: [] };
  let market = "resale";
  try { market = localStorage.getItem(STORE + "-market") || "resale"; } catch {}
  let cityNames = { lviv: "Львів" };
  const cityName = (k) => cityNames[k || "lviv"];
  let mine = {};
  let tab = "unseen";
  try { mine = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { mine = {}; }
  try { tab = localStorage.getItem(STORE + "-tab") || "unseen"; } catch {}
  const saveMine = () => { try { localStorage.setItem(STORE, JSON.stringify(mine)); } catch {} };
  const setMine = (id, patch) => { mine[id] = { ...mine[id], ...patch }; saveMine(); };

  const PICKED = new Set(["Цікаво", "Подзвонити", "Дивився"]);
  const REJECTED = new Set(["Відхилено", "Продано"]);
  const isSeen = (it) => !!(mine[it.id]?.seen || mine[it.id]?.status);
  const inTab = (it, t) => {
    const st = mine[it.id]?.status;
    if (t === "unseen") return !isSeen(it) && !REJECTED.has(st);
    if (t === "picked") return !!mine[it.id]?.fav || PICKED.has(st);
    if (t === "rejected") return REJECTED.has(st);
    return !REJECTED.has(st);
  };

  const filterIds = ["f-city", "f-rooms", "f-price", "f-area", "f-sort", "f-old", "f-repair", "f-owner", "f-budget", "f-fresh", "f-nopanel", "f-inactive", "f-pets", "f-nocomm", "f-power"];
  const saveFilters = () => {
    const f = {};
    filterIds.forEach((id) => { const e = $(id); f[id] = e.type === "checkbox" ? e.checked : e.value; });
    try { localStorage.setItem(STORE + "-filters3" + (deal === "rent" ? "-rent" : ""), JSON.stringify(f)); } catch {}
  };
  const readFilters = () => { try { return JSON.parse(localStorage.getItem(STORE + "-filters3" + (deal === "rent" ? "-rent" : "")) || "{}"); } catch { return {}; } };

  let deal = /^#rent/.test(location.hash) ? "rent" : (() => { try { return localStorage.getItem(STORE + "-deal") || "sale"; } catch { return "sale"; } })();
  if (/^#id=/.test(location.hash)) deal = "sale";
  const cardLink = (it) => location.origin + location.pathname + (deal === "rent" ? "#rent&id=" : "#id=") + encodeURIComponent(it.id);
  const focusFromHash = () => { const m = location.hash.match(/^#(?:rent&)?id=(.+)$/); return m ? decodeURIComponent(m[1]) : null; };
  const perMonth = () => (deal === "rent" ? "/міс" : "");
  let focusId = focusFromHash();
  const copy = async (text, msg) => {
    try { await navigator.clipboard.writeText(text); toast(msg); }
    catch { window.prompt("Скопіюйте:", text); }
  };

  const mapsLink = (it) => `https://www.google.com/maps/search/?api=1&query=${it.lat},${it.lon}`;
  function mapSlide(it) {
    const z = it.geo_approx ? 15 : 16;
    const n = 2 ** z, lat = it.lat * Math.PI / 180;
    const xt = (it.lon + 180) / 360 * n;
    const yt = (1 - Math.log(Math.tan(lat) + 1 / Math.cos(lat)) / Math.PI) / 2 * n;
    const bx = Math.floor(xt) - 1, by = Math.floor(yt) - 1;
    const a = el("a", "mapslide");
    a.href = mapsLink(it); a.target = "_blank"; a.rel = "noopener";
    a.title = "Відкрити на карті";
    const tiles = el("div", "tiles");
    tiles.style.left = `calc(50% - ${(xt - bx) * 256}px)`;
    tiles.style.top = `calc(50% - ${(yt - by) * 256}px)`;
    for (let dx = 0; dx < 3; dx++) for (let dy = 0; dy < 3; dy++) {
      const img = el("img");
      img.loading = "lazy"; img.alt = "";
      img.onerror = () => { img.style.visibility = "hidden"; };
      img.src = `https://tile.openstreetmap.org/${z}/${bx + dx}/${by + dy}.png`;
      img.style.left = dx * 256 + "px"; img.style.top = dy * 256 + "px";
      tiles.append(img);
    }
    a.append(tiles);
    if (it.geo_approx) a.append(el("span", "zone"));
    a.append(el("span", "pin", "📍"));
    a.append(el("span", "maplabel", it.geo_approx ? "📍 Приблизно · відкрити карту" : "📍 Відкрити карту"));
    a.append(el("span", "attr", "© OpenStreetMap"));
    return a;
  }
  const roomsMatch = (rooms, f) => {
    if (!f || f === "all") return true;
    if (rooms == null) return true;
    if (f.endsWith("+")) return rooms >= Number(f.slice(0, -1));
    return rooms === Number(f);
  };

  const daysAgo = (iso) => Math.floor((Date.now() - new Date(iso + "T00:00:00").getTime()) / 864e5);

  const badgeList = (it) => {
    const f = it.flags || {};
    const b = [];
    if (f.old_fund) b.push(["🏛 Старий фонд", "good"]);
    if (f.needs_repair && deal === "sale") b.push(["🔨 Під ремонт", "good"]);
    if (deal === "rent") {
      if (f.pets_ok) b.push(["🐾 Можна з тваринами", "good"]);
      else if (f.no_pets) b.push(["🚫 Без тварин", "warn"]);
      if (f.no_commission) b.push(["🙅 Без комісії", "good"]);
      if (f.power) b.push(["🔋 Генератор / укриття", "good"]);
      if (it.furnished) b.push(["🛋 " + it.furnished, ""]);
    }
    if (f.owner) b.push(["👤 Власник", "good"]);
    if (it.verified) b.push(["✅ Перевірено DIM.RIA", "good"]);
    if (f.new_build && market === "new") {
      if (f.under_construction) b.push(["🚧 Будується / переуступка", "warn"]);
      else if (f.ready) b.push(["✅ Здано", "good"]);
    }
    if (f.spacious) b.push(["📐 Простора", ""]);
    if (f.fresh_renovation) b.push(["✨ Свіжий ремонт", ""]);
    if (f.over_budget) b.push(["💸 Вище бюджету", "warn"]);
    if (f.apartments) b.push(["⚠ Апартаменти", "bad"]);
    if (f.under_construction && market !== "new") b.push(["⚠ Будується / переуступка", "bad"]);
    if (it.active === false) b.push(["Зникло з сайту", "bad"]);
    return b;
  };

  // ---------- досьє для чату з Claude: усе, що зібрано про оголошення
  const FLAG_NAMES = {
    old_fund: "старий фонд", needs_repair: "під ремонт / старий ремонт", fresh_renovation: "свіжий ремонт", owner: "від власника",
    apartments: "АПАРТАМЕНТИ (можливо нежитловий статус)", under_construction: "будується / переуступка", spacious: "простора",
    over_budget: "вище бюджету", new_build: "новобудова", ready: "здано", pets_ok: "можна з тваринами", no_pets: "без тварин",
    no_commission: "без комісії", power: "генератор / укриття",
  };
  const dossier = (it) => {
    const f = it.flags || {};
    const L = [];
    const kv = (k, v) => { if (v != null && v !== "" && !(Array.isArray(v) && !v.length)) L.push(`- ${k}: ${Array.isArray(v) ? v.join(", ") : v}`); };
    L.push(`## Квартира #${it.id} (${deal === "rent" ? "оренда" : "купівля"})`, "");
    kv("Назва", it.title);
    kv("Ціна", `$${fmt(it.price_usd)}${perMonth()}${uah(it.price_usd) ? ` (≈ ${fmt(uah(it.price_usd))} грн за курсом НБУ ${data.usd_rate})` : ""}${it.area && deal !== "rent" ? `, $${fmt(it.price_usd / it.area)}/м²` : ""}`);
    kv("Ціна в оголошенні", it.price_orig);
    const h = it.price_history || [];
    if (h.length > 1) kv("Історія ціни", h.map((x) => `${x.date}: $${fmt(x.usd)}`));
    kv("Кімнат", it.rooms); kv("Площа, м²", it.area);
    kv("Поверх", it.floor ? `${it.floor}${it.floors ? " з " + it.floors : ""}` : null);
    kv("Місто", cityName(it.city)); kv("Район", it.district); kv("Адреса", it.address !== cityName(it.city) ? it.address : null);
    kv("Будинок", it.building); kv("Рік побудови", it.year_built); kv("Стан / ремонт", it.condition); kv("Меблі", it.furnished);
    if (it.lat != null) kv("Координати", `${it.lat}, ${it.lon}${it.geo_approx ? " (приблизно)" : ""} — ${mapsLink(it)}`);
    kv("Позначки", Object.entries(f).filter(([, v]) => v).map(([k]) => FLAG_NAMES[k] || k));
    kv("Продавець", it.seller); kv("Телефони", it.phones);
    kv("Перевірено DIM.RIA", it.verified ? "так" : null);
    kv("Свіжість", it.fresh != null ? `${it.fresh}/100${(it.fresh_why || []).length ? " — " + it.fresh_why.join("; ") : ""}` : null);
    kv("Опубліковано", it.published); kv("Оновлено на сайті", it.updated); kv("Знайдено нами", it.first_seen); kv("Востаннє бачили", it.last_seen);
    if (it.active === false) kv("Статус", "зникло з сайту (можливо продано/здано)");
    kv("Джерело", `${it.source}: ${it.url}`);
    kv("Також на", (it.also_on || []).map((x) => `${x.source}: ${x.url}`));
    kv("Наша картка", cardLink(it));
    const m = mine[it.id] || {};
    kv("Мій статус", m.status); kv("Моя нотатка", m.note); kv("В обраних", m.fav ? "так" : null);
    if (it.description) L.push("", "### Опис з оголошення", it.description);
    if ((it.photos || []).length) L.push("", `### Фото (${it.photos.length})`, ...it.photos.map((u) => `- ${u}`));
    return L.join("\n");
  };
  const cxDossier = (cx) => {
    const L = [`## ${/^ЖК/i.test(cx.name) ? "" : "ЖК "}${cx.name} #${cx.id} (новобудова)`, ""];
    const kv = (k, v) => { if (v != null && v !== "") L.push(`- ${k}: ${v}`); };
    kv("Забудовник", cx.developer); kv("Адреса", cx.street); kv("Місто", cityName(cx.city));
    if (cx.lat != null) kv("Координати", `${cx.lat}, ${cx.lon} — ${mapsLink(cx)}`);
    kv("Стан", (STATUS[cx.status] || [cx.status])[0] + (cx.status === "partly" ? ` (${cx.ready_sections} з ${cx.ready_sections + cx.building_sections} секцій)` : ""));
    kv("Здача", cx.release); kv("Поверховість", cx.floors_max || cx.floors_max_cat);
    kv("Ціна за м²", cx.price_m2_usd ? `від $${fmt(cx.price_m2_usd)}` : null);
    kv("Квартир у продажу", cx.available);
    if (cx.installment) kv("Розтермінування", `${cx.installment.months ? "до " + cx.installment.months + " міс." : "так"}${cx.installment.first_pct ? ", перший внесок від " + cx.installment.first_pct + "%" : ""}`);
    kv("Кредит банку", cx.credit ? "так" : null); kv("Житловий ваучер", cx.voucher ? "так" : null); kv("Акція", cx.promo);
    for (const [r, x] of Object.entries(cx.rooms || {})) kv(`${r}${r === "4" ? "+" : ""} кімн.`, `від $${fmt(x.from_usd)}, ${x.area_min}–${x.area_max} м², ${x.plans} планувань`);
    kv("Сторінка ЖК", cx.url); kv("Наша картка", cardLink(cx));
    if (cx.promo_text) L.push("", "### Опис", cx.promo_text);
    if ((cx.plans || []).length) {
      L.push("", "### Планування в продажу");
      for (const p of cx.plans) L.push(`- ${p.rooms} кімн., ${p.area} м², $${fmt(p.price_usd)} ($${fmt(p.m2_usd)}/м²), поверхи ${p.floors || "?"}${p.building_floors ? " з " + p.building_floors : ""}${p.release ? ", здача " + p.release : ""}${p.photo ? " — план: " + p.photo : ""}`);
    }
    if ((cx.photos || []).length) L.push("", "### Фото", ...cx.photos.map((u) => `- ${u}`));
    return L.join("\n");
  };
  const toClaude = (text) => copy("Ось квартира, яку я розглядаю. Що думаєш?\n\n" + text, "Скопійовано — встав у чат з Claude");

  const shareText = (it) => {
    const f = it.flags || {};
    const tags = [f.old_fund && "🏛 старий фонд", f.needs_repair && "🔨 під ремонт", f.owner && "👤 від власника"].filter(Boolean).join(", ");
    const ppm = it.price_usd && it.area ? ` ($${fmt(it.price_usd / it.area)}/м²)` : "";
    return [
      `🏠 ${it.title || "Квартира"}`,
      `💵 $${fmt(it.price_usd)}${perMonth()}${deal === "rent" ? "" : ppm}${uah(it.price_usd) ? ` · ≈ ${fmt(uah(it.price_usd))} грн` : ""}`,
      `🛏 ${it.rooms || "?"} кімн. · ${it.area || "?"} м²${it.floor ? ` · ${it.floor}${it.floors ? "/" + it.floors : ""} пов.` : ""}`,
      it.district ? `📍 ${it.district}` : "",
      tags,
      (it.phones || []).length ? `📞 ${it.phones.join(", ")}${it.seller ? " (" + it.seller + ")" : ""}` : "",
      `🔗 На нашому сайті: ${cardLink(it)}`,
      it.link_kind === "search" ? "" : `📎 Оголошення: ${it.url}`,
    ].filter(Boolean).join("\n");
  };

  // ---------- share
  let shareItem = null;
  const toast = (msg) => {
    const t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 1800);
  };
  function share(it, override) {
    shareItem = it;
    shareItem._text = override || null;
    const text = override || shareText(it);
    $("sh-sub").textContent = `${it.title || it.name || "Квартира"}${it.price_usd ? " · $" + fmt(it.price_usd) : ""} · код #${it.id}`;
    $("sh-wa").href = "https://wa.me/?text=" + encodeURIComponent(text);
    $("sh-tg").href = "https://t.me/share/url?url=" + encodeURIComponent(cardLink(it)) + "&text=" + encodeURIComponent(text);
    $("sh-vb").href = "viber://forward?text=" + encodeURIComponent(text);
    $("sh-native").hidden = !navigator.share;
    $("share-sheet").hidden = false;
  }
  $("sh-native").onclick = async () => {
    if (!shareItem) return;
    $("share-sheet").hidden = true;
    try { await navigator.share({ title: shareItem.title || shareItem.name, text: shareItem._text || shareText(shareItem) }); } catch {}
  };
  $("sh-copy-card").onclick = () => { if (shareItem) copy(cardLink(shareItem), "Посилання на картку скопійовано"); $("share-sheet").hidden = true; };
  $("sh-close").onclick = () => { $("share-sheet").hidden = true; };
  $("share-sheet").onclick = (e) => { if (e.target.id === "share-sheet") $("share-sheet").hidden = true; };
  $("sh-copy").onclick = () => { if (shareItem) copy(shareItem._text || shareText(shareItem), "Скопійовано"); $("share-sheet").hidden = true; };

  // ---------- card
  function buildCard(it) {
    const node = $("card-tpl").content.firstElementChild.cloneNode(true);
    const st = mine[it.id] || {};
    if (REJECTED.has(st.status)) node.classList.add("rejected");
    if (it.active === false) node.classList.add("inactive");

    // gallery
    const strip = node.querySelector(".strip");
    const photos = (it.photos || []).filter(Boolean);
    const pcount = node.querySelector(".pcount");
    const hasMap = it.lat != null && it.lon != null;
    if (photos.length) {
      photos.forEach((src, i) => {
        if (i === 1 && hasMap) strip.append(mapSlide(it));
        const img = el("img");
        img.loading = i ? "lazy" : "eager";
        img.src = src;
        img.alt = `${it.title || ""} — фото ${i + 1}`;
        img.onerror = () => { img.remove(); if (!strip.querySelector("img")) strip.append(el("span", "noimg", "🏠")); };
        strip.append(img);
      });
      if (photos.length === 1 && hasMap) strip.append(mapSlide(it));
      const total = strip.children.length;
      if (total > 1) pcount.textContent = `1 / ${total}`;
      else pcount.remove();
      strip.addEventListener("scroll", () => {
        const i = Math.round(strip.scrollLeft / strip.clientWidth) + 1;
        if (pcount.isConnected) pcount.textContent = `${i} / ${strip.children.length}`;
      }, { passive: true });
      if (total > 1) {
        const gal = node.querySelector(".gallery");
        for (const [cls, dir, sym] of [["prev", -1, "‹"], ["next", 1, "›"]]) {
          const b = el("button", "navbtn " + cls, sym);
          b.setAttribute("aria-label", dir < 0 ? "Попереднє фото" : "Наступне фото");
          b.onclick = () => strip.scrollBy({ left: dir * strip.clientWidth, behavior: "smooth" });
          gal.append(b);
        }
      }
    } else if (hasMap) {
      strip.append(mapSlide(it));
      pcount.remove();
    } else {
      strip.append(el("span", "noimg", "🏠"));
      pcount.remove();
    }
    const favBtn = node.querySelector(".fav");
    const paintFav = () => { const on = !!mine[it.id]?.fav; favBtn.classList.toggle("on", on); favBtn.textContent = on ? "★" : "☆"; favBtn.setAttribute("aria-label", on ? "Прибрати з обраних" : "Додати в обрані"); };
    paintFav();
    favBtn.onclick = () => {
      const on = !mine[it.id]?.fav;
      setMine(it.id, { fav: on, seen: true });
      paintFav();
      toast(on ? "⭐ Додано в обрані" : "Прибрано з обраних");
      if (tab === "picked" && !on) { node.classList.add("leaving"); setTimeout(render, 300); } else updateCounts();
    };
    node.querySelector(".src").textContent = it.source;
    if (!isSeen(it) && it.first_seen && daysAgo(it.first_seen) <= 2) node.querySelector(".newtag").hidden = false;

    // price
    node.querySelector(".price strong").textContent = `$${fmt(it.price_usd)}${perMonth()}`;
    node.querySelector(".ppm").textContent = it.area ? `$${fmt(it.price_usd / it.area)}/м²` : "";
    const u = uah(it.price_usd);
    if (u) node.querySelector(".uah").textContent = deal === "rent" ? `≈ ${fmt(u)} грн/міс` : `≈ ${fmt(u)} грн` + (it.area ? ` · ${fmt(u / it.area)} грн/м²` : "");
    if (deal === "rent") node.querySelector(".ppm").textContent = "";
    const hist = it.price_history || [];
    if (hist.length > 1 && hist[0].usd > it.price_usd) node.querySelector(".price").append(el("span", "drop", `↓ було $${fmt(hist[0].usd)}`));
    node.querySelector(".title").textContent = it.title || "Квартира";
    const code = node.querySelector(".code");
    code.textContent = `#${it.id} · скопіювати посилання`;
    code.onclick = () => copy(cardLink(it), "Посилання на картку скопійовано");
    const warn = node.querySelector(".linkwarn");
    if (it.link_kind === "search") { warn.hidden = false; warn.textContent = "⚠ Прямого посилання на це оголошення немає — кнопка веде на сторінку пошуку, де його треба знайти за описом."; }
    if (it.link_kind === "building") { warn.hidden = false; warn.textContent = "⚠ Посилання веде на сторінку будинку на LUN, де є це оголошення серед інших."; }
    if (it.id === focusId) node.classList.add("focus");
    if (it.suspicious) {
      node.classList.add("suspicious");
      const st = node.querySelector(".stale");
      st.hidden = false;
      st.textContent = `⚠ Можливо застаріле (свіжість ${it.fresh}/100): ${(it.fresh_why || []).join("; ")}`;
    }
    const others = (it.also_on || []).filter((x) => x.source !== "Вручну");
    if (others.length) {
      const ao = node.querySelector(".alsoon");
      ao.hidden = false;
      ao.append("🔁 Також на:");
      for (const x of others) {
        const a = el("a", null, x.source);
        a.href = x.url; a.target = "_blank"; a.rel = "noopener";
        ao.append(a);
      }
    }

    // details
    const dl = node.querySelector(".details");
    const rows = [
      ["Кімнат", it.rooms],
      ["Площа", it.area ? `${it.area} м²` : null],
      ["Поверх", it.floor ? `${it.floor}${it.floors ? " з " + it.floors : ""}` : null],
      ["Місто", cityName(it.city)],
      ["Район", it.district],
      ["Будинок", it.building],
      ["Опубліковано", it.published],
      ["Ціна в оголошенні", it.price_orig],
      ["Знайдено", it.first_seen],
      ["Оновлено на сайті", it.updated && it.updated !== it.published ? it.updated : null],
      ["Свіжість", it.fresh != null ? `${it.fresh}/100` : null],
    ];
    for (const [k, v] of rows) {
      if (v == null || v === "") continue;
      const d = el("div");
      d.append(el("dt", null, k), el("dd", null, String(v)));
      dl.append(d);
    }
    if (hasMap) {
      const d = el("div"), dd = el("dd"), a = el("a", "maplink", it.geo_approx ? "📍 Карта (приблизно)" : "📍 На карті");
      a.href = mapsLink(it); a.target = "_blank"; a.rel = "noopener";
      dd.append(a); d.append(el("dt", null, "Локація"), dd); dl.append(d);
    }

    const badges = node.querySelector(".badges");
    for (const [text, cls] of badgeList(it)) badges.append(el("span", "badge " + cls, text));

    // description
    const desc = node.querySelector(".desc");
    const more = node.querySelector(".more");
    if (it.description) {
      desc.textContent = it.description;
      requestAnimationFrame(() => {
        if (desc.scrollHeight > desc.clientHeight + 4) {
          more.hidden = false;
          more.onclick = () => { const o = desc.classList.toggle("open"); more.textContent = o ? "Згорнути" : "Показати повністю"; };
        }
      });
    } else {
      node.querySelector(".descwrap").remove();
    }

    // contacts
    const c = node.querySelector(".contact");
    c.append(el("span", "who", "👤 " + (it.seller || (it.flags?.owner ? "Власник" : "Продавець"))));
    if (it.phones && it.phones.length) {
      for (const p of it.phones) {
        const a = el("a", null, "📞 " + p);
        a.href = "tel:" + p.replace(/[^\d+]/g, "");
        c.append(a);
      }
    } else {
      c.append(el("span", "hint", "Телефон — кнопка «Показати телефон» в оголошенні"));
    }

    // actions
    const open = node.querySelector(".open");
    open.href = it.url;
    if (it.link_kind === "search") open.textContent = "Пошук";
    if (it.link_kind === "building") open.textContent = "Будинок";
    open.onclick = () => { setMine(it.id, { seen: true }); seenBtn.classList.add("on"); updateCounts(); };
    node.querySelector(".share").onclick = () => share(it);
    node.querySelector(".claude").onclick = () => toClaude(dossier(it));
    const seenBtn = node.querySelector(".seen");
    if (isSeen(it)) seenBtn.classList.add("on");
    seenBtn.onclick = () => {
      const now = !mine[it.id]?.seen;
      setMine(it.id, { seen: now });
      seenBtn.classList.toggle("on", now);
      toast(now ? "Позначено переглянутим" : "Повернуто в не переглянуті");
      if (tab === "unseen" && now) { node.classList.add("leaving"); setTimeout(render, 300); } else updateCounts();
    };

    const sel = node.querySelector(".status");
    sel.value = st.status || "";
    sel.onchange = () => { setMine(it.id, { status: sel.value, seen: true }); render(); };
    const note = node.querySelector(".note");
    note.value = st.note || "";
    note.onchange = () => setMine(it.id, { note: note.value });
    return node;
  }

  // ---------- новобудови від забудовників
  const STATUS = { ready: ["✅ Здано", "good"], partly: ["🟡 Частково здано", "warn"], building: ["🚧 Будується", "warn"] };
  const cxFrom = (cx) => {
    const v = Object.entries(cx.rooms || {}).filter(([r]) => Number(r) >= (data.criteria?.rooms_min || 2)).map(([, x]) => x.from_usd);
    return v.length ? Math.min(...v) : null;
  };
  const cxShareText = (cx) => {
    const lines = [`🏗 ${cx.name}${cx.developer ? " (" + cx.developer + ")" : ""}`, cx.street ? `📍 ${cx.street}` : ""];
    for (const [r, x] of Object.entries(cx.rooms || {})) lines.push(`🛏 ${r}${r === "4" ? "+" : ""} кімн.: від $${fmt(x.from_usd)} · ${x.area_min}–${x.area_max} м²`);
    if (!cx.rooms || !Object.keys(cx.rooms).length) lines.push(`💵 від $${fmt(cx.price_m2_usd)}/м²`);
    lines.push((STATUS[cx.status] || [""])[0] + (cx.release ? ` · здача ${cx.release}` : ""));
    if (cx.installment) lines.push(`📆 Розтермінування${cx.installment.months ? " до " + cx.installment.months + " міс." : ""}${cx.installment.first_pct ? ", перший внесок від " + cx.installment.first_pct + "%" : ""}`);
    lines.push(`🔗 На нашому сайті: ${cardLink(cx)}`, `📎 ЖК: ${cx.url}`);
    return lines.filter(Boolean).join("\n");
  };
  function buildComplex(cx) {
    const node = $("card-tpl").content.firstElementChild.cloneNode(true);
    if (cx.id === focusId) node.classList.add("focus");
    const strip = node.querySelector(".strip");
    const pics = (cx.photos && cx.photos.length ? cx.photos : [cx.photo]).filter(Boolean);
    pics.forEach((src, i) => {
      if (i === 1 && cx.lat != null) strip.append(mapSlide({ ...cx, geo_approx: false }));
      const img = el("img"); img.src = src; img.alt = `${cx.name} — фото ${i + 1}`; img.loading = i ? "lazy" : "eager";
      img.onerror = () => img.remove();
      strip.append(img);
    });
    if (pics.length <= 1 && cx.lat != null) strip.append(mapSlide({ ...cx, geo_approx: false }));
    if (!strip.children.length) strip.append(el("span", "noimg", "🏗"));
    const pc = node.querySelector(".pcount");
    if (strip.children.length > 1) {
      pc.textContent = `1 / ${strip.children.length}`;
      strip.addEventListener("scroll", () => { pc.textContent = `${Math.round(strip.scrollLeft / strip.clientWidth) + 1} / ${strip.children.length}`; }, { passive: true });
    } else pc.remove();
    node.querySelector(".src").textContent = cx.source;
    const fav = node.querySelector(".fav");
    const paint = () => { const on = !!mine[cx.id]?.fav; fav.classList.toggle("on", on); fav.textContent = on ? "★" : "☆"; };
    paint();
    fav.onclick = () => { setMine(cx.id, { fav: !mine[cx.id]?.fav }); paint(); toast(mine[cx.id].fav ? "⭐ Додано в обрані" : "Прибрано з обраних"); };

    const from = cxFrom(cx);
    node.querySelector(".price strong").textContent = from ? `від $${fmt(from)}` : `від $${fmt(cx.price_m2_usd)}/м²`;
    node.querySelector(".ppm").textContent = from ? `від $${fmt(cx.price_m2_usd)}/м²` : "";
    const u = uah(from);
    if (u) node.querySelector(".uah").textContent = `≈ від ${fmt(u)} грн`;
    node.querySelector(".title").textContent = cx.name;
    const code = node.querySelector(".code");
    code.textContent = `#${cx.id} · скопіювати посилання`;
    code.onclick = () => copy(cardLink(cx), "Посилання скопійовано");
    const sub = el("p", "cx-sub", [cx.developer, cx.street].filter(Boolean).join(" · "));
    node.querySelector(".title").after(sub);

    const dl = node.querySelector(".details");
    const rooms = Object.entries(cx.rooms || {});
    if (rooms.length) {
      const ul = el("ul", "cx-rooms");
      for (const [r, x] of rooms) {
        const li = el("li", x.from_usd > (data.criteria?.max_price_usd || Infinity) ? "over" : "");
        li.append(el("strong", null, `${r}${r === "4" ? "+" : ""} кімн. — від $${fmt(x.from_usd)}`),
          el("span", null, `${x.area_min === x.area_max ? x.area_min : x.area_min + "–" + x.area_max} м² · ${x.plans} план.`));
        ul.append(li);
      }
      dl.replaceWith(ul);
    } else {
      const d = el("div"); d.append(el("dt", null, "Ціни за кімнатністю"), el("dd", null, "на сторінці ЖК")); dl.append(d);
    }
    const facts = el("dl", "details");
    const addRow = (k, v) => { const d = el("div"); d.append(el("dt", null, k), v instanceof Node ? v : el("dd", null, String(v))); facts.append(d); };
    if (cx.street || cx.lat != null) {
      const dd = el("dd");
      if (cx.street) dd.append(cx.street + " ");
      if (cx.lat != null) { const a = el("a", "maplink", "📍 На карті"); a.href = mapsLink(cx); a.target = "_blank"; a.rel = "noopener"; dd.append(a); }
      addRow("Локація", dd);
    }
    if (cityName(cx.city)) addRow("Місто", cityName(cx.city));
    const fl = cx.floors_max || cx.floors_max_cat;
    if (fl) addRow("Поверховість", `${cx.floors_min_cat && cx.floors_min_cat !== fl ? cx.floors_min_cat + "–" : "до "}${fl} пов.`);
    if (cx.release) addRow("Здача", cx.release);
    if (cx.area_min) addRow("Площі", `${cx.area_min}–${cx.area_max} м²`);
    node.querySelector(".title").parentElement.querySelector(".cx-sub").after(facts);

    if (cx.plans && cx.plans.length) {
      const det = el("details", "plans");
      det.open = cx.id === focusId;
      det.append(el("summary", null, `📐 Планування (${cx.plans.length}${Object.values(cx.rooms || {}).reduce((a, x) => a + x.plans, 0) > cx.plans.length ? " з " + Object.values(cx.rooms || {}).reduce((a, x) => a + x.plans, 0) : ""})`));
      const grid = el("div", "plan-grid");
      const maxP = data.criteria?.max_price_usd || Infinity;
      for (const p of cx.plans) {
        const a = el("a", "plan" + (p.price_usd > maxP ? " over" : ""));
        a.href = p.photo || cx.url; a.target = "_blank"; a.rel = "noopener";
        if (p.photo) { const im = el("img"); im.src = p.photo; im.loading = "lazy"; im.alt = `План ${p.name || ""}`; im.onerror = () => { im.style.visibility = "hidden"; }; a.append(im); }
        a.append(el("strong", null, `$${fmt(p.price_usd)}`));
        a.append(el("span", null, `${p.rooms} кімн. · ${p.area} м²`));
        a.append(el("span", null, `пов. ${p.floors || "?"}${p.building_floors ? " з " + p.building_floors : ""}${p.release ? " · " + p.release : ""}`));
        grid.append(a);
      }
      det.append(grid);
      const all = el("a", "maplink", "Усі планування на DOM.RIA →");
      all.href = cx.url.replace(/\/?$/, "/") + "planirovki/"; all.target = "_blank"; all.rel = "noopener";
      det.append(all);
      node.querySelector(".badges").after(det);
    }

    const badges = node.querySelector(".badges");
    const [st, cls] = STATUS[cx.status] || ["", ""];
    if (st) badges.append(el("span", "badge " + cls, st + (cx.status !== "ready" && cx.release ? ` · здача ${cx.release}` : "")));
    if (cx.status === "partly") badges.append(el("span", "badge", `${cx.ready_sections} з ${cx.ready_sections + cx.building_sections} секцій здано`));
    if (cx.installment) badges.append(el("span", "badge good", `📆 Розтермінування${cx.installment.months ? " до " + cx.installment.months + " міс." : ""}${cx.installment.first_pct ? ", від " + cx.installment.first_pct + "%" : ""}`));
    if (cx.credit) badges.append(el("span", "badge", "🏦 Кредит банку"));
    if (cx.voucher) badges.append(el("span", "badge", "🎟 Житловий ваучер"));
    if (cx.verified) badges.append(el("span", "badge good", "✅ Перевірено DIM.RIA"));
    if (cx.promo) badges.append(el("span", "badge warn", "🎁 " + cx.promo));
    if (cx.available) badges.append(el("span", "badge", `${cx.available} квартир у продажу`));
    const desc = node.querySelector(".desc");
    if (cx.promo_text) desc.textContent = cx.promo_text; else node.querySelector(".descwrap").remove();
    node.querySelector(".contact").remove();
    node.querySelector(".mine").remove();
    node.querySelector(".seen").remove();
    const open = node.querySelector(".open");
    open.href = cx.url; open.textContent = "Відкрити ЖК";
    node.querySelector(".share").onclick = () => share(cx, cxShareText(cx));
    node.querySelector(".claude").onclick = () => toClaude(cxDossier(cx));
    return node;
  }
  function renderComplexes() {
    const box = $("complexes");
    box.hidden = market !== "new" || deal === "rent";
    if (box.hidden) return;
    const city = $("f-city").value;
    const maxP = data.criteria?.max_price_usd || Infinity;
    let list = (data.complexes || []).filter((cx) => {
      if (city && (cx.city || "lviv") !== city) return false;
      if ($("cx-ready").checked && cx.status === "building") return false;
      if ($("cx-install").checked && !cx.installment) return false;
      if ($("cx-budget").checked) { const f = cxFrom(cx); if (f == null || f > maxP) return false; }
      return true;
    });
    if (focusId && focusId.startsWith("zk-")) list = (data.complexes || []).filter((cx) => cx.id === focusId);
    list.sort((a, b) => (cxFrom(a) ?? 1e9) - (cxFrom(b) ?? 1e9) || (a.price_m2_usd || 0) - (b.price_m2_usd || 0));
    $("cx-count").textContent = `ЖК: ${list.length} з ${(data.complexes || []).length}`;
    const g = $("cx-list");
    g.replaceChildren(...(list.length ? list.map(buildComplex) : [el("p", "empty", "Немає ЖК за цими умовами.")]));
  }
  ["cx-budget", "cx-ready", "cx-install"].forEach((id) => $(id).addEventListener("change", () => { try { localStorage.setItem(STORE + "-" + id, $(id).checked ? "1" : "0"); } catch {} renderComplexes(); }));
  ["cx-budget", "cx-ready", "cx-install"].forEach((id) => { try { const v = localStorage.getItem(STORE + "-" + id); if (v != null) $(id).checked = v === "1"; } catch {} });
  const setMarket = (m) => {
    market = m;
    try { localStorage.setItem(STORE + "-market", m); } catch {}
    document.querySelectorAll("#market button").forEach((b) => b.classList.toggle("active", b.dataset.market === m));
  };
  document.querySelectorAll("#market button").forEach((b) => { b.onclick = () => { clearFocus(); setMarket(b.dataset.market); render(); }; });

  // ---------- list
  // Панельний будинок: поле «Будинок» (OLX/DOM.RIA пишуть «Панельний») або явна згадка в назві/описі.
  // «Сонячні панелі» тощо не чіпаємо — шукаємо лише «панельн… будинок» / «панельк…».
  const PANEL_TEXT = /панельн\S*\s+(будин|будівл|дім|дом)|панельк/i;
  const isPanel = (it) => /панел/i.test(it.building || "") || PANEL_TEXT.test(`${it.title || ""} ${it.description || ""}`);

  function filtered() {
    const rooms = $("f-rooms").value;
    const city = $("f-city").value;
    const maxP = Number($("f-price").value) || Infinity;
    const minA = Number($("f-area").value) || 0;
    const budget = data.criteria?.budget_usd || Infinity;
    return data.items.filter((it) => {
      const f = it.flags || {};
      if (!roomsMatch(it.rooms, rooms)) return false;
      if (city && (it.city || "lviv") !== city) return false;
      if ((it.price_usd || 0) > maxP) return false;
      if ((it.area || 0) < minA) return false;
      if ($("f-old").checked && !f.old_fund) return false;
      if ($("f-repair").checked && !f.needs_repair) return false;
      if ($("f-owner").checked && !f.owner) return false;
      if ($("f-budget").checked && (it.price_usd || 0) > budget) return false;
      if (!$("f-inactive").checked && it.active === false) return false;
      if (it.dup_of && data.items.some((x) => x.id === it.dup_of)) return false;
      if ($("f-fresh").checked && it.suspicious) return false;
      if ($("f-nopanel").checked && isPanel(it)) return false;
      if (deal === "sale" && (market === "new") !== !!f.new_build) return false;
      if (deal === "rent") {
        if ($("f-pets").checked && !f.pets_ok) return false;
        if ($("f-nocomm").checked && !(f.no_commission || f.owner)) return false;
        if ($("f-power").checked && !f.power) return false;
      }
      return true;
    });
  }

  function updateCounts() {
    const base = filtered();
    document.querySelectorAll("#tabs button").forEach((b) => {
      b.querySelector("span").textContent = base.filter((it) => inTab(it, b.dataset.tab)).length;
      b.classList.toggle("active", b.dataset.tab === tab);
    });
  }

  function render() {
    saveFilters();
    updateCounts();
    const ppm = (it) => (it.price_usd && it.area ? it.price_usd / it.area : Infinity);
    const sorters = {
      score: (a, b) => (b.score || 0) - (a.score || 0),
      new: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || (b.published || "").localeCompare(a.published || ""),
      price: (a, b) => (a.price_usd || 0) - (b.price_usd || 0),
      ppm: (a, b) => ppm(a) - ppm(b),
      area: (a, b) => (b.area || 0) - (a.area || 0),
    };
    if (focusId && focusId.startsWith("zk-")) setMarket("new");
    else if (focusId) { const f = data.items.find((it) => it.id === focusId); if (f) setMarket(f.flags?.new_build ? "new" : "resale"); }
    document.querySelectorAll("#market button").forEach((b) => b.classList.toggle("active", b.dataset.market === market));
    renderComplexes();
    const focused = focusId ? data.items.filter((it) => it.id === focusId) : [];
    $("focusbar").hidden = !focused.length && !(focusId && focusId.startsWith("zk-"));
    if (focusId && focusId.startsWith("zk-")) { $("list").replaceChildren(); $("count").textContent = ""; return; }
    const items = focused.length ? focused : filtered().filter((it) => inTab(it, tab)).sort(sorters[$("f-sort").value] || sorters.score);
    $("count").textContent = `Знайдено ${items.length} з ${data.items.length}`;
    const list = $("list");
    list.replaceChildren();
    if (!items.length) {
      const msg = { unseen: "Усе переглянуто 👌 Нові варіанти з'являться тут.", picked: "Тут будуть квартири зі статусом «Цікаво», «Подзвонити» чи «Дивився».", rejected: "Відхилених поки немає." }[tab] || "Нічого не знайдено за цими фільтрами.";
      list.append(el("p", "empty", msg));
      return;
    }
    const PAGE = 24;
    let shown = 0;
    const more = el("button", "btn more-btn");
    const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) showMore(); }, { rootMargin: "800px" }) : null;
    function showMore() {
      const frag = document.createDocumentFragment();
      for (const it of items.slice(shown, shown + PAGE)) frag.append(buildCard(it));
      shown = Math.min(shown + PAGE, items.length);
      more.before(frag);
      if (shown >= items.length) { more.remove(); if (io) io.disconnect(); }
      else more.textContent = `Показати ще (${items.length - shown})`;
    }
    more.onclick = showMore;
    list.append(more);
    showMore();
    if (io && more.isConnected) io.observe(more);
  }

  const clearFocus = () => { focusId = null; history.replaceState(null, "", location.pathname + (deal === "rent" ? "#rent" : "")); };
  $("focus-clear").onclick = () => { clearFocus(); render(); };

  document.querySelectorAll("#tabs button").forEach((b) => {
    b.onclick = () => { clearFocus(); tab = b.dataset.tab; try { localStorage.setItem(STORE + "-tab", tab); } catch {} render(); window.scrollTo({ top: 0 }); };
  });

  const datasets = {};
  async function fetchDeal(d) {
    const files = d === "rent" ? ["data/rent.json"] : ["data/listings.json", "data/listings-home.json"];
    const parts = (await Promise.all(files.map((f) =>
      fetch(f + "?ts=" + Date.now()).then((r) => (r.ok ? r.json() : null)).catch(() => null)))).filter(Boolean);
    if (!parts.length) return null;
    const byId = new Map();
    for (const p of parts) for (const it of p.items || []) {
      const prev = byId.get(it.id);
      if (!prev || (it.last_seen || "") >= (prev.last_seen || "")) byId.set(it.id, it);
    }
    const cities = new Map();
    parts.forEach((p) => (p.cities || []).forEach((c) => cities.set(c.key, c)));
    return {
      ...parts[0],
      items: [...byId.values()],
      complexes: parts.flatMap((p) => p.complexes || []),
      cities: [...cities.values()],
      updated: parts.map((p) => p.updated).filter(Boolean).sort().pop(),
      updated_home: parts[1]?.updated,
      errors: parts.flatMap((p) => p.errors || []),
    };
  }

  let listenersOn = false;
  async function showDeal(d) {
    deal = d;
    try { localStorage.setItem(STORE + "-deal", d); } catch {}
    document.body.dataset.deal = d;
    document.querySelectorAll("#deal button").forEach((b) => b.classList.toggle("active", b.dataset.deal === d));
    if (!datasets[d]) {
      $("list").replaceChildren();
      $("count").replaceChildren(el("span", "spinner"), d === "rent" ? " Завантажую оренду…" : " Завантажую оголошення…");
      datasets[d] = await fetchDeal(d);
    }
    if (!datasets[d]) {
      $("meta").textContent = d === "rent" ? "Дані оренди ще не зібрані — вони з'являться після найближчого запуску збору." : "Не вдалося завантажити дані.";
      $("count").textContent = "";
      data = { items: [], complexes: [] };
      return;
    }
    data = datasets[d];
    const upd = data.updated ? new Date(data.updated).toLocaleString("uk-UA", { dateStyle: "medium", timeStyle: "short" }) : "—";
    const c = data.criteria || {};
    $("meta").textContent = d === "rent"
      ? `Оренда · бюджет $${fmt(c.budget_usd)}/міс (показую до $${fmt(c.max_price_usd)}) · оновлено ${upd}`
      : `Купівля · бюджет $${fmt(c.budget_usd)} (показую до $${fmt(c.max_price_usd)}) · оновлено ${upd}`;
    if (data.updated_home) $("meta").append(` · OLX і rieltor з домашнього ПК: ${new Date(data.updated_home).toLocaleString("uk-UA", { dateStyle: "medium", timeStyle: "short" })}`);
    if (data.errors && data.errors.length) $("meta").append(el("span", "err", ` · помилки збору: ${data.errors.join("; ")}`));
    $("f-price").placeholder = c.max_price_usd || "";
    const cities = data.cities && data.cities.length ? data.cities : [{ key: "lviv", name: "Львів" }];
    cityNames = Object.fromEntries(cities.map((x) => [x.key, x.name]));
    $("f-city").replaceChildren(...(cities.length > 1 ? [new Option("Усі міста", "")] : []), ...cities.map((x) => new Option(x.name, x.key)));
    const saved = readFilters();
    filterIds.forEach((id) => {
      const e = $(id);
      if (e.type === "checkbox") e.checked = !!saved[id];
      else if (id === "f-rooms") e.value = saved[id] || c.default_rooms || "2+";
      else if (id === "f-sort") e.value = saved[id] || "score";
      else e.value = saved[id] || "";
      if (!listenersOn) e.addEventListener(e.tagName === "INPUT" && e.type !== "checkbox" ? "input" : "change", render);
    });
    listenersOn = true;
    if (!$("f-rooms").value) $("f-rooms").value = c.default_rooms || "2+";
    if ($("f-city").selectedIndex < 0) $("f-city").selectedIndex = 0;
    render();
  }
  document.querySelectorAll("#deal button").forEach((b) => {
    b.onclick = () => {
      if (b.dataset.deal === deal) return;
      focusId = null;
      history.replaceState(null, "", location.pathname + (b.dataset.deal === "rent" ? "#rent" : ""));
      showDeal(b.dataset.deal);
      window.scrollTo({ top: 0 });
    };
  });
  window.addEventListener("hashchange", () => {
    const d = /^#rent/.test(location.hash) ? "rent" : /^#id=/.test(location.hash) ? "sale" : deal;
    focusId = focusFromHash();
    if (d !== deal) showDeal(d); else render();
  });
  showDeal(deal);
})();