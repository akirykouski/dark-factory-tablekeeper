const app = (() => {
  let state = {
    token: localStorage.getItem('token'),
    displayName: localStorage.getItem('displayName'),
    restaurants: [],
    restaurantMap: {},
    currentSearch: null,
    currentSearchSeq: 0,
    selectedSlot: null,
    selectedSlotSeq: null,
    lastBookingKey: null,
    lastBookingBody: null,
    lastBookingResult: null,
    lastSearchSeq: 0,
  };

  const API = {
    async call(method, path, body = null, options = {}) {
      const headers = {
        'Content-Type': 'application/json',
      };
      if (options.idempotencyKey) {
        headers['Idempotency-Key'] = options.idempotencyKey;
      }
      if (state.token) {
        headers['Authorization'] = `Bearer ${state.token}`;
      }

      const init = { method, headers };
      if (body) init.body = JSON.stringify(body);

      try {
        const response = await fetch(path, init);
        const data = await response.json();
        return { status: response.status, data };
      } catch (e) {
        throw new Error('Network error: ' + e.message);
      }
    },

    async getRestaurants() {
      const { data } = await API.call('GET', '/restaurants');
      return data.restaurants;
    },

    async getRestaurant(id) {
      const { data } = await API.call('GET', `/restaurants/${id}`);
      return data;
    },

    async getAvailability(restaurantId, date, partySize) {
      const { data } = await API.call('GET', `/availability?restaurant_id=${restaurantId}&date=${date}&party_size=${partySize}`);
      return data;
    },

    async createReservation(body, key) {
      return API.call('POST', '/reservations', body, { idempotencyKey: key });
    },

    async getReservation(ref) {
      return API.call('GET', `/reservations/${ref}`);
    },

    async cancelReservation(ref) {
      return API.call('POST', `/reservations/${ref}/cancel`, {});
    },

    async signup(email, password, displayName) {
      return API.call('POST', '/auth/signup', { email, password, display_name: displayName });
    },

    async login(email, password) {
      return API.call('POST', '/auth/login', { email, password });
    },
  };

  const initApp = async () => {
    try {
      if (state.restaurants.length === 0) {
        const restaurants = await API.getRestaurants();
        state.restaurants = restaurants;
        state.restaurantMap = {};
        for (const r of restaurants) {
          try {
            const full = await API.getRestaurant(r.id);
            state.restaurantMap[r.id] = full;
          } catch (e) {
            state.restaurantMap[r.id] = r;
          }
        }
      }
    } catch (e) {
      console.error('Failed to load restaurants:', e);
    }
  };

  const router = (path) => {
    history.pushState({}, '', path);
    render();
  };

  const getCurrentPage = () => {
    const path = window.location.pathname;
    if (path === '/signup') return 'signup';
    if (path === '/login') return 'login';
    if (path === '/lookup') return 'lookup';
    return 'home';
  };

  const showError = (containerId, message) => {
    let container = document.getElementById(containerId);
    if (!container) {
      container = document.createElement('div');
      container.id = containerId;
      container.className = 'error-message visible';
      container.dataset.testid = 'auth-error';
      document.querySelector('[data-testid="auth-error"]')?.parentNode?.insertBefore(container, document.querySelector('[data-testid="auth-error"]'));
    }
    container.textContent = message;
    container.classList.add('visible');
    if (!container.parentNode) {
      const page = document.getElementById(`page-${getCurrentPage()}`);
      page?.insertBefore(container, page.firstChild);
    }
  };

  const hideError = (containerId) => {
    const container = document.getElementById(containerId);
    if (container) {
      container.remove();
    }
  };

  const render = async () => {
    await initApp();

    const currentPage = getCurrentPage();

    // Update navbar
    const currentUserEl = document.getElementById('current-user');
    const logoutBtn = document.getElementById('logout-button');
    const authNav = document.getElementById('auth-nav');

    if (state.token && state.displayName) {
      currentUserEl.textContent = state.displayName;
      currentUserEl.style.display = 'inline';
      logoutBtn.style.display = 'inline-block';
      authNav.style.display = 'none';
    } else {
      currentUserEl.style.display = 'none';
      logoutBtn.style.display = 'none';
      authNav.style.display = 'flex';
    }

    logoutBtn.onclick = () => {
      state.token = null;
      state.displayName = null;
      localStorage.removeItem('token');
      localStorage.removeItem('displayName');
      router('/');
    };

    document.getElementById('page-home').style.display = currentPage === 'home' ? 'block' : 'none';
    document.getElementById('page-signup').style.display = currentPage === 'signup' ? 'block' : 'none';
    document.getElementById('page-login').style.display = currentPage === 'login' ? 'block' : 'none';
    document.getElementById('page-lookup').style.display = currentPage === 'lookup' ? 'block' : 'none';

    if (currentPage === 'home') {
      renderHome();
    }
  };

  const renderHome = () => {
    const select = document.getElementById('restaurant-select');
    select.innerHTML = state.restaurants.map(r => `<option value="${r.id}">${r.name}</option>`).join('');

    const today = new Date().toISOString().split('T')[0];
    document.getElementById('date-input').value = today;
  };

  const search = async () => {
    const restaurantId = document.getElementById('restaurant-select').value;
    const date = document.getElementById('date-input').value;
    const partySize = document.getElementById('party-size-input').value;

    if (!restaurantId || !date || !partySize) return;

    state.currentSearchSeq++;
    const searchSeq = state.currentSearchSeq;

    hideError('search-auth-error');

    try {
      const availability = await API.getAvailability(restaurantId, date, partySize);

      if (searchSeq !== state.currentSearchSeq) return;

      state.currentSearch = {
        restaurantId,
        date,
        partySize,
        availability,
      };

      const restaurant = await API.getRestaurant(restaurantId);

      if (searchSeq !== state.currentSearchSeq) return;

      renderGrid(restaurant, availability, partySize);
    } catch (e) {
      showError('search-auth-error', 'Search failed: ' + e.message);
    }
  };

  const renderGrid = (restaurant, availability, partySize) => {
    const grid = document.getElementById('availability-grid');
    const noSlots = document.getElementById('no-slots');

    if (!availability.slots || availability.slots.length === 0) {
      grid.innerHTML = '';
      noSlots.classList.add('visible');
      const bookingForm = document.getElementById('booking-form-container');
      if (bookingForm) bookingForm.remove();
      return;
    }

    noSlots.classList.remove('visible');
    grid.innerHTML = '';

    const tableMap = {};
    restaurant.tables.forEach(t => {
      tableMap[t.id] = t;
    });

    availability.slots.forEach(slot => {
      const hhmm = slot.starts_at_local.split('T')[1];

      const availableIds = new Set(slot.available_table_ids);
      restaurant.tables.forEach(table => {
        const cellId = `slot-${table.id}-${hhmm}`;
        const isAvailable = availableIds.has(table.id);
        const cell = createGridCell(cellId, table.label || table.id, hhmm, isAvailable, () => {
          if (isAvailable) {
            if (!state.token) {
              showError('search-auth-error', 'Please sign in to book');
              return;
            }
            selectSlot([table.id], table.label || table.id, hhmm, state.currentSearchSeq);
          }
        });
        grid.appendChild(cell);
      });

      const displayedPairs = new Set();
      slot.available_options?.forEach(option => {
        if (option.table_ids.length === 2) {
          const [id1, id2] = option.table_ids;
          const pairKey = [id1, id2].sort().join(',');
          if (displayedPairs.has(pairKey)) return;
          displayedPairs.add(pairKey);

          const label1 = tableMap[id1]?.label || id1;
          const label2 = tableMap[id2]?.label || id2;
          const cellId = `slot-${id1}+${id2}-${hhmm}`;
          const isAvailable = true;
          const cell = createGridCell(cellId, `${label1} + ${label2}`, hhmm, isAvailable, () => {
            if (!state.token) {
              showError('search-auth-error', 'Please sign in to book');
              return;
            }
            selectSlot(option.table_ids, `${label1} + ${label2}`, hhmm, state.currentSearchSeq);
          });
          grid.appendChild(cell);
        }
      });
    });
  };

  const createGridCell = (id, label, hhmm, available, onClick) => {
    const cell = document.createElement('div');
    cell.dataset.testid = id;
    cell.dataset.available = available ? 'true' : 'false';
    cell.className = 'grid-cell';
    cell.innerHTML = `
      <div class="grid-cell-label">${escapeHtml(label)}</div>
      <div class="grid-cell-time">${hhmm}</div>
    `;
    if (available) {
      cell.onclick = onClick;
    }
    return cell;
  };

  const selectSlot = (tableIds, tableLabel, hhmm, searchSeq) => {
    // Ignore if this was from a late search
    if (searchSeq !== state.currentSearchSeq) return;

    state.selectedSlot = {
      tableIds,
      tableLabel,
      hhmm,
      restaurantId: state.currentSearch.restaurantId,
    };
    state.selectedSlotSeq = searchSeq;

    const container = document.getElementById('booking-form-container');
    if (container) container.remove();

    const newForm = document.createElement('div');
    newForm.id = 'booking-form-container';
    newForm.className = 'booking-form-container visible';
    newForm.data-testid = 'booking-form';
    newForm.innerHTML = `
      <div id="booking-error" class="error-message"></div>
      <div id="booking-uncertain" class="warning-message"></div>
      <div class="booking-summary">
        <div class="booking-summary-label">Selected</div>
        <div id="booking-summary" data-testid="booking-summary" class="booking-summary-value">${escapeHtml(tableLabel)} at ${hhmm}</div>
      </div>
      <form id="booking-form" onsubmit="app.submitBooking(event)">
        <div class="form-group">
          <label for="booking-party-size">Number of guests</label>
          <input type="number" id="booking-party-size" data-testid="booking-party-size" min="1" value="${state.currentSearch.partySize}" required>
        </div>
        <button type="submit" class="btn-primary" data-testid="booking-submit">Book now</button>
      </form>
    `;

    document.getElementById('page-home').appendChild(newForm);

    state.lastBookingKey = null;
    state.lastBookingBody = null;
    state.lastBookingResult = null;
  };

  const submitBooking = async (e) => {
    e.preventDefault();

    if (!state.selectedSlot || state.selectedSlotSeq !== state.currentSearchSeq) return;

    const partySize = parseInt(document.getElementById('booking-party-size').value);
    const body = {
      restaurant_id: state.selectedSlot.restaurantId,
      table_ids: state.selectedSlot.tableIds,
      starts_at_local: `${state.currentSearch.date}T${state.selectedSlot.hhmm}`,
      party_size: partySize,
    };

    const isNewBooking = JSON.stringify(body) !== JSON.stringify(state.lastBookingBody);
    if (isNewBooking) {
      state.lastBookingKey = generateIdempotencyKey();
      state.lastBookingBody = JSON.parse(JSON.stringify(body));
      state.lastBookingResult = null;
    }

    const errorEl = document.getElementById('booking-error');
    const uncertainEl = document.getElementById('booking-uncertain');
    if (errorEl) errorEl.remove();
    if (uncertainEl) uncertainEl.remove();

    const button = e.target.querySelector('[type="submit"]');
    button.disabled = true;

    try {
      const result = await API.createReservation(body, state.lastBookingKey);

      if (result.status === 201 || result.status === 200) {
        state.lastBookingResult = result.data;
        const restaurant = state.restaurantMap[state.currentSearch.restaurantId];
        const tableLabels = result.data.table_ids.map(tid => {
          const table = restaurant?.tables?.find(t => t.id === tid);
          return table?.label || tid;
        }).join(', ');

        const confEl = document.createElement('div');
        confEl.id = 'confirmation';
        confEl.className = 'confirmation visible';
        confEl.dataset.testid = 'confirmation';
        confEl.innerHTML = `
          <h3>✓ Booking confirmed!</h3>
          <div>Your reservation reference:</div>
          <div id="confirmation-reference" data-testid="confirmation-reference" class="confirmation-reference">${result.data.reference}</div>
          <div id="confirmation-details" data-testid="confirmation-details" class="confirmation-details">${restaurant?.name || 'Restaurant'} • ${state.selectedSlot.tableLabel} • ${state.selectedSlot.hhmm}</div>
          <div id="confirmation-tables" data-testid="confirmation-tables" class="confirmation-tables">${tableLabels}</div>
        `;
        document.getElementById('booking-form-container').appendChild(confEl);

        await search();
      } else if (result.status === 409) {
        const err = document.createElement('div');
        err.id = 'booking-error';
        err.className = 'error-message visible';
        err.data-testid = 'booking-error';
        err.textContent = 'Table is no longer available. Please try another.';
        document.getElementById('booking-form-container').insertBefore(err, document.getElementById('booking-form-container').firstChild);
        await search();
      } else {
        const err = document.createElement('div');
        err.id = 'booking-error';
        err.className = 'error-message visible';
        err.data-testid = 'booking-error';
        err.textContent = `Booking failed: ${result.data?.error?.code || 'Unknown error'}`;
        document.getElementById('booking-form-container').insertBefore(err, document.getElementById('booking-form-container').firstChild);
      }
    } catch (e) {
      const uncEl = document.createElement('div');
      uncEl.id = 'booking-uncertain';
      uncEl.className = 'warning-message visible';
      uncEl.data-testid = 'booking-uncertain';
      uncEl.textContent = 'Unable to confirm your booking. Your reservation may have been created. Please try again.';
      document.getElementById('booking-form-container').insertBefore(uncEl, document.getElementById('booking-form-container').firstChild);
    } finally {
      button.disabled = false;
    }
  };

  const lookupReservation = async () => {
    const ref = document.getElementById('lookup-reference-input').value.trim().toUpperCase();
    if (!ref) return;

    const errorEl = document.getElementById('lookup-error');
    const detailEl = document.getElementById('reservation-detail');
    if (errorEl) errorEl.remove();
    if (detailEl) detailEl.remove();

    if (!state.token) {
      const err = document.createElement('div');
      err.id = 'lookup-error';
      err.className = 'reservation-error visible';
      err.data-testid = 'reservation-error';
      err.textContent = 'Please sign in to look up your booking.';
      document.getElementById('page-lookup').appendChild(err);
      return;
    }

    try {
      const result = await API.getReservation(ref);

      if (result.status === 200) {
        const res = result.data;
        const restaurant = state.restaurantMap[res.restaurant_id] || state.restaurants.find(r => r.id === res.restaurant_id) || { name: 'Restaurant' };
        const tables = res.table_ids.map(tid => {
          const table = restaurant.tables?.find(t => t.id === tid);
          return table?.label || tid;
        }).join(', ');

        const detail = document.createElement('div');
        detail.id = 'reservation-detail';
        detail.className = 'reservation-detail visible';
        detail.data-testid = 'reservation-detail';
        detail.dataset.ref = ref;
        const cancelBtn = res.status === 'confirmed' ? `<button id="reservation-cancel-button" data-testid="reservation-cancel-button" class="btn-danger" onclick="app.cancelReservation()" style="margin-top: var(--spacing-lg);">Cancel reservation</button>` : '';
        detail.innerHTML = `
          <h2>Reservation details</h2>
          <div class="reservation-item">
            <span class="reservation-item-label">Restaurant</span>
            <span id="detail-restaurant" class="reservation-item-value">${restaurant.name}</span>
          </div>
          <div class="reservation-item">
            <span class="reservation-item-label">Tables</span>
            <span id="detail-tables" data-testid="reservation-tables" class="reservation-item-value">${tables}</span>
          </div>
          <div class="reservation-item">
            <span class="reservation-item-label">Time</span>
            <span id="detail-time" class="reservation-item-value">${res.starts_at_local.split('T')[1]}</span>
          </div>
          <div class="reservation-item">
            <span class="reservation-item-label">Party size</span>
            <span id="detail-party" class="reservation-item-value">${res.party_size}</span>
          </div>
          <div class="reservation-item">
            <span class="reservation-item-label">Status</span>
            <span id="detail-status" data-testid="reservation-status" class="reservation-item-value ${res.status}">${res.status}</span>
          </div>
          ${cancelBtn}
        `;
        document.getElementById('page-lookup').appendChild(detail);
      } else {
        const err = document.createElement('div');
        err.id = 'lookup-error';
        err.className = 'reservation-error visible';
        err.data-testid = 'reservation-error';
        err.textContent = 'Booking not found.';
        document.getElementById('page-lookup').appendChild(err);
      }
    } catch (e) {
      const err = document.createElement('div');
      err.id = 'lookup-error';
      err.className = 'reservation-error visible';
      err.data-testid = 'reservation-error';
      err.textContent = 'Error: ' + e.message;
      document.getElementById('page-lookup').appendChild(err);
    }
  };

  const cancelReservation = async () => {
    const detail = document.getElementById('reservation-detail');
    const ref = detail?.dataset.ref;
    if (!ref) return;

    const btn = document.getElementById('reservation-cancel-button');
    btn.disabled = true;

    try {
      const result = await API.cancelReservation(ref);

      if (result.status === 200) {
        const statusEl = document.getElementById('detail-status');
        statusEl.textContent = 'cancelled';
        statusEl.className = 'reservation-item-value cancelled';
        btn.remove();
      } else if (result.status === 409) {
        const errorEl = document.getElementById('lookup-error');
        if (errorEl) errorEl.remove();
        const err = document.createElement('div');
        err.id = 'lookup-error';
        err.className = 'reservation-error visible';
        err.data-testid = 'reservation-error';
        err.textContent = 'Cannot cancel: booking is within the cancellation window.';
        document.getElementById('page-lookup').appendChild(err);
      } else {
        const errorEl = document.getElementById('lookup-error');
        if (errorEl) errorEl.remove();
        const err = document.createElement('div');
        err.id = 'lookup-error';
        err.className = 'reservation-error visible';
        err.data-testid = 'reservation-error';
        err.textContent = 'Cancellation failed.';
        document.getElementById('page-lookup').appendChild(err);
      }
    } catch (e) {
      const errorEl = document.getElementById('lookup-error');
      if (errorEl) errorEl.remove();
      const err = document.createElement('div');
      err.id = 'lookup-error';
      err.className = 'reservation-error visible';
      err.data-testid = 'reservation-error';
      err.textContent = 'Error: ' + e.message;
      document.getElementById('page-lookup').appendChild(err);
    } finally {
      btn.disabled = false;
    }
  };

  const submitSignup = async (e) => {
    e.preventDefault();

    const email = document.getElementById('signup-email').value;
    const password = document.getElementById('signup-password').value;
    const displayName = document.getElementById('signup-display-name').value;

    hideError('signup-auth-error');

    try {
      const result = await API.signup(email, password, displayName);

      if (result.status === 201) {
        state.token = result.data.token;
        state.displayName = result.data.display_name;
        localStorage.setItem('token', state.token);
        localStorage.setItem('displayName', state.displayName);
        router('/');
      } else {
        showError('signup-auth-error', result.data?.error?.message || 'Signup failed');
      }
    } catch (e) {
      showError('signup-auth-error', 'Error: ' + e.message);
    }
  };

  const submitLogin = async (e) => {
    e.preventDefault();

    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;

    hideError('login-auth-error');

    try {
      const result = await API.login(email, password);

      if (result.status === 200) {
        state.token = result.data.token;
        state.displayName = result.data.display_name;
        localStorage.setItem('token', state.token);
        localStorage.setItem('displayName', state.displayName);
        router('/');
      } else {
        showError('login-auth-error', result.data?.error?.message || 'Login failed');
      }
    } catch (e) {
      showError('login-auth-error', 'Error: ' + e.message);
    }
  };

  const generateIdempotencyKey = () => {
    return 'key_' + Math.random().toString(36).substr(2, 9);
  };

  const escapeHtml = (text) => {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  };

  window.addEventListener('popstate', render);

  render();

  return {
    router,
    search,
    selectSlot,
    submitBooking,
    lookupReservation,
    cancelReservation,
    submitSignup,
    submitLogin,
  };
})();
