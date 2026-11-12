(() => {
  const $ = (id) => document.getElementById(id);
  const STORE = "kvartyra-lviv-v1";
  const uah = (usd) => (usd && data.usd_rate ? Math.round(usd * data.usd_rate / 1000) * 1000 : null);
  const fmt = (n) => (n == null ? "?" : Math.round(n).toLocaleString("uk-UA"));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  let data = { items: [] };
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
    if (t === "picked") return PICKED.has(st);
    if (t === "rejected") return REJECTED.has(st);
    return !REJECTED.has(st);
  };

  const filterIds = ["f-rooms", "f-price", "f-area", "f-sort", "f-old", "f-repair", "f-owner", "f-budget", "f-inactive"];
  const saveFilters = () => {
    const f = {};
    filterIds.forEach((id) => { const e = $(id); f[id] = e.type === "checkbox" ? e.checked : e.value; });
    try { localStorage.setItem(STORE + "-filters", JSON.stringify(f)); } catch {}
  };
  const readFilters = () => { try { return JSON.parse(localStorage.getItem(STORE + "-filters") || "{}"); } catch { return {}; } };

  const cardLink = (it) => location.origin + location.pathname + "#id=" + encodeURIComponent(it.id);
  const focusFromHash = () => { const m = location.hash.match(/^#id=(.+)$/); return m ? decodeURIComponent(m[1]) : null; };
  let focusId = focusFromHash();
  const copy = async (text, msg) => {
    try { await navigator.clipboard.writeText(text); toast(msg); }
    catch { window.prompt("Скопіюйте:", text); }
  };

  const daysAgo = (iso) => Math.floor((Date.now() - new Date(iso + "T00:00:00").getTime()) / 864e5);

  const badgeList = (it) => {
    const f = it.flags || {};
    const b = [];
    if (f.old_fund) b.push(["🏛 Старий фонд", "good"]);
    if (f.needs_repair) b.push(["🔨 Під ремонт", "good"]);
    if (f.owner) b.push(["👤 Власник", "good"]);
    if (f.spacious) b.push(["📐 Простора", ""]);
    if (f.fresh_renovation) b.push(["✨ Свіжий ремонт", ""]);
    if (f.over_budget) b.push(["💸 Вище бюджету", "warn"]);
    if (f.apartments) b.push(["⚠ Апартаменти", "bad"]);
    if (f.under_construction) b.push(["⚠ Будується / переуступка", "bad"]);
    if (it.active === false) b.push(["Зникло з сайту", "bad"]);
    return b;
  };

  const shareText = (it) => {
    const f = it.flags || {};
    const tags = [f.old_fund && "🏛 старий фонд", f.needs_repair && "🔨 під ремонт", f.owner && "👤 від власника"].filter(Boolean).join(", ");
    const ppm = it.price_usd && it.area ? ` ($${fmt(it.price_usd / it.area)}/м²)` : "";
    return [
      `🏠 ${it.title || "Квартира"}`,
      `💵 $${fmt(it.price_usd)}${ppm}${uah(it.price_usd) ? ` · ≈ ${fmt(uah(it.price_usd))} грн` : ""}`,
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
  function share(it) {
    shareItem = it;
    const text = shareText(it);
    $("sh-sub").textContent = `${it.title || "Квартира"} · $${fmt(it.price_usd)} · код #${it.id}`;
    $("sh-wa").href = "https://wa.me/?text=" + encodeURIComponent(text);
    $("sh-tg").href = "https://t.me/share/url?url=" + encodeURIComponent(cardLink(it)) + "&text=" + encodeURIComponent(text);
    $("sh-vb").href = "viber://forward?text=" + encodeURIComponent(text);
    $("sh-native").hidden = !navigator.share;
    $("share-sheet").hidden = false;
  }
  $("sh-native").onclick = async () => {
    if (!shareItem) return;
    $("share-sheet").hidden = true;
    try { await navigator.share({ title: shareItem.title, text: shareText(shareItem) }); } catch {}
  };
  $("sh-copy-card").onclick = () => { if (shareItem) copy(cardLink(shareItem), "Посилання на картку скопійовано"); $("share-sheet").hidden = true; };
  $("sh-close").onclick = () => { $("share-sheet").hidden = true; };
  $("share-sheet").onclick = (e) => { if (e.target.id === "share-sheet") $("share-sheet").hidden = true; };
  $("sh-copy").onclick = () => { if (shareItem) copy(shareText(shareItem), "Скопійовано"); $("share-sheet").hidden = true; };

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
    if (photos.length) {
      photos.forEach((src, i) => {
        const img = el("img");
        img.loading = i ? "lazy" : "eager";
        img.src = src;
        img.alt = `${it.title || ""} — фото ${i + 1}`;
        img.onerror = () => { img.remove(); if (!strip.querySelector("img")) strip.append(el("span", "noimg", "🏠")); };
        strip.append(img);
      });
      if (photos.length > 1) pcount.textContent = `1 / ${photos.length}`;
      else pcount.remove();
      strip.addEventListener("scroll", () => {
        const i = Math.round(strip.scrollLeft / strip.clientWidth) + 1;
        pcount.textContent = `${i} / ${photos.length}`;
      }, { passive: true });
      if (photos.length > 1) {
        const gal = node.querySelector(".gallery");
        for (const [cls, dir, sym] of [["prev", -1, "‹"], ["next", 1, "›"]]) {
          const b = el("button", "navbtn " + cls, sym);
          b.setAttribute("aria-label", dir < 0 ? "Попереднє фото" : "Наступне фото");
          b.onclick = () => strip.scrollBy({ left: dir * strip.clientWidth, behavior: "smooth" });
          gal.append(b);
        }
      }
    } else {
      strip.append(el("span", "noimg", "🏠"));
      pcount.remove();
    }
    node.querySelector(".src").textContent = it.source;
    if (!isSeen(it) && it.first_seen && daysAgo(it.first_seen) <= 2) node.querySelector(".newtag").hidden = false;

    // price
    node.querySelector(".price strong").textContent = `$${fmt(it.price_usd)}`;
    node.querySelector(".ppm").textContent = it.area ? `$${fmt(it.price_usd / it.area)}/м²` : "";
    const u = uah(it.price_usd);
    if (u) node.querySelector(".uah").textContent = `≈ ${fmt(u)} грн` + (it.area ? ` · ${fmt(u / it.area)} грн/м²` : "");
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

    // details
    const dl = node.querySelector(".details");
    const rows = [
      ["Кімнат", it.rooms],
      ["Площа", it.area ? `${it.area} м²` : null],
      ["Поверх", it.floor ? `${it.floor}${it.floors ? " з " + it.floors : ""}` : null],
      ["Район", it.district],
      ["Будинок", it.building],
      ["Опубліковано", it.published],
      ["Ціна в оголошенні", it.price_orig],
      ["Знайдено", it.first_seen],
    ];
    for (const [k, v] of rows) {
      if (v == null || v === "") continue;
      const d = el("div");
      d.append(el("dt", null, k), el("dd", null, String(v)));
      dl.append(d);
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

  // ---------- list
  function filtered() {
    const rooms = $("f-rooms").value;
    const maxP = Number($("f-price").value) || Infinity;
    const minA = Number($("f-area").value) || 0;
    const budget = data.criteria?.budget_usd || Infinity;
    return data.items.filter((it) => {
      const f = it.flags || {};
      if (rooms && String(it.rooms) !== rooms) return false;
      if ((it.price_usd || 0) > maxP) return false;
      if ((it.area || 0) < minA) return false;
      if ($("f-old").checked && !f.old_fund) return false;
      if ($("f-repair").checked && !f.needs_repair) return false;
      if ($("f-owner").checked && !f.owner) return false;
      if ($("f-budget").checked && (it.price_usd || 0) > budget) return false;
      if (!$("f-inactive").checked && it.active === false) return false;
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
    const focused = focusId ? data.items.filter((it) => it.id === focusId) : [];
    $("focusbar").hidden = !focused.length;
    const items = focused.length ? focused : filtered().filter((it) => inTab(it, tab)).sort(sorters[$("f-sort").value] || sorters.score);
    $("count").textContent = `Показано ${items.length} з ${data.items.length}`;
    const list = $("list");
    list.replaceChildren();
    if (!items.length) {
      const msg = { unseen: "Усе переглянуто 👌 Нові варіанти з'являться тут.", picked: "Тут будуть квартири зі статусом «Цікаво», «Подзвонити» чи «Дивився».", rejected: "Відхилених поки немає." }[tab] || "Нічого не знайдено за цими фільтрами.";
      list.append(el("p", "empty", msg));
      return;
    }
    for (const it of items) list.append(buildCard(it));
  }

  const clearFocus = () => { focusId = null; history.replaceState(null, "", location.pathname); };
  $("focus-clear").onclick = () => { clearFocus(); render(); };
  window.addEventListener("hashchange", () => { focusId = focusFromHash(); render(); window.scrollTo({ top: 0 }); });
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.onclick = () => { clearFocus(); tab = b.dataset.tab; try { localStorage.setItem(STORE + "-tab", tab); } catch {} render(); window.scrollTo({ top: 0 }); };
  });

  async function load() {
    try {
      const r = await fetch("data/listings.json?ts=" + Date.now());
      data = await r.json();
    } catch {
      $("meta").textContent = "Не вдалося завантажити дані.";
      return;
    }
    const upd = data.updated ? new Date(data.updated).toLocaleString("uk-UA", { dateStyle: "medium", timeStyle: "short" }) : "—";
    const c = data.criteria || {};
    $("meta").textContent = `${c.rooms_min}–${c.rooms_max} кімнати · бюджет $${fmt(c.budget_usd)} (показую до $${fmt(c.max_price_usd)}) · оновлено ${upd}`;
    if (data.errors && data.errors.length) $("meta").append(el("span", "err", ` · помилки збору: ${data.errors.join("; ")}`));
    $("f-price").placeholder = c.max_price_usd || "";
    const saved = readFilters();
    filterIds.forEach((id) => {
      const e = $(id);
      if (saved[id] !== undefined) { if (e.type === "checkbox") e.checked = saved[id]; else e.value = saved[id]; }
      e.addEventListener(e.tagName === "INPUT" && e.type !== "checkbox" ? "input" : "change", render);
    });
    render();
  }
  load();
})();
