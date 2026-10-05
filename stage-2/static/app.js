const app = (() => {
  let state = {
    token: localStorage.getItem('token'),
    displayName: localStorage.getItem('displayName'),
    restaurants: [],
    restaurantMap: {},
    currentSearch: null,
    currentSearchSeq: 0,
    selectedSlot: null,
    lastBookingKey: null,
    lastBookingBody: null,
    lastBookingResult: null,
    lastSearchSeq: 0,
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
            // Fallback to basic data
            state.restaurantMap[r.id] = r;
          }
        }
      }
    } catch (e) {
      console.error('Failed to load restaurants:', e);
    }
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

    // Clear logout button click handler and re-add
    logoutBtn.onclick = () => {
      state.token = null;
      state.displayName = null;
      localStorage.removeItem('token');
      localStorage.removeItem('displayName');
      router('/');
    };

    // Show/hide pages
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

    try {
      document.getElementById('search-error').style.display = 'none';
      const availability = await API.getAvailability(restaurantId, date, partySize);

      // Ignore if a newer search has started
      if (searchSeq !== state.currentSearchSeq) return;

      state.currentSearch = {
        restaurantId,
        date,
        partySize,
        availability,
      };

      // Get restaurant details for labels
      const restaurant = await API.getRestaurant(restaurantId);

      // Re-check search seq in case another request completed while we were waiting
      if (searchSeq !== state.currentSearchSeq) return;

      renderGrid(restaurant, availability, partySize);
    } catch (e) {
      document.getElementById('search-error').textContent = 'Search failed: ' + e.message;
      document.getElementById('search-error').style.display = 'block';
    }
  };

  const renderGrid = (restaurant, availability, partySize) => {
    const grid = document.getElementById('availability-grid');
    const noSlots = document.getElementById('no-slots');

    if (!availability.slots || availability.slots.length === 0) {
      grid.innerHTML = '';
      noSlots.classList.add('visible');
      document.getElementById('booking-form-container').classList.remove('visible');
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

      // Single tables
      const availableIds = new Set(slot.available_table_ids);
      restaurant.tables.forEach(table => {
        const cellId = `slot-${table.id}-${hhmm}`;
        const isAvailable = availableIds.has(table.id);
        const cell = createGridCell(cellId, table.label || table.id, hhmm, isAvailable, () => {
          if (isAvailable) {
            if (!state.token) {
              document.getElementById('search-error').textContent = 'Please sign in to book';
              document.getElementById('search-error').style.display = 'block';
              return;
            }
            selectSlot([table.id], table.label || table.id, hhmm);
          }
        });
        grid.appendChild(cell);
      });

      // Combination pairs - get from available_options
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
          const isAvailable = true; // It's in available_options, so it's available
          const cell = createGridCell(cellId, `${label1} + ${label2}`, hhmm, isAvailable, () => {
            if (!state.token) {
              document.getElementById('search-error').textContent = 'Please sign in to book';
              document.getElementById('search-error').style.display = 'block';
              return;
            }
            selectSlot(option.table_ids, `${label1} + ${label2}`, hhmm);
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

  const selectSlot = (tableIds, tableLabel, hhmm) => {
    state.selectedSlot = {
      tableIds,
      tableLabel,
      hhmm,
      restaurantId: state.currentSearch.restaurantId,
    };

    const container = document.getElementById('booking-form-container');
    container.classList.add('visible');

    document.getElementById('booking-summary').textContent = `${tableLabel} at ${hhmm}`;
    document.getElementById('booking-party-size').value = state.currentSearch.partySize;
    document.getElementById('booking-error').style.display = 'none';
    document.getElementById('booking-uncertain').style.display = 'none';
    document.getElementById('confirmation').classList.remove('visible');

    // Generate new idempotency key for new form
    state.lastBookingKey = null;
    state.lastBookingBody = null;
    state.lastBookingResult = null;

    document.getElementById('booking-form').onsubmit = (e) => submitBooking(e);
  };

  const submitBooking = async (e) => {
    e.preventDefault();

    if (!state.selectedSlot) return;

    const partySize = parseInt(document.getElementById('booking-party-size').value);
    const body = {
      restaurant_id: state.selectedSlot.restaurantId,
      table_ids: state.selectedSlot.tableIds,
      starts_at_local: `${state.currentSearch.date}T${state.selectedSlot.hhmm}`,
      party_size: partySize,
    };

    // Generate idempotency key if this is a new booking
    const isNewBooking = JSON.stringify(body) !== JSON.stringify(state.lastBookingBody);
    if (isNewBooking) {
      state.lastBookingKey = generateIdempotencyKey();
      state.lastBookingBody = JSON.parse(JSON.stringify(body));
      state.lastBookingResult = null;
    }

    const button = e.target.querySelector('[type="submit"]');
    button.disabled = true;

    document.getElementById('booking-error').style.display = 'none';
    document.getElementById('booking-uncertain').style.display = 'none';

    try {
      const result = await API.createReservation(body, state.lastBookingKey);

      if (result.status === 201 || result.status === 200) {
        state.lastBookingResult = result.data;
        document.getElementById('confirmation-reference').textContent = result.data.reference;
        document.getElementById('confirmation-details').textContent =
          `${state.currentSearch.restaurantId === 'r_anker' ? 'Anker' : 'Hudson'} • ${state.selectedSlot.tableLabel} • ${state.selectedSlot.hhmm}`;
        document.getElementById('confirmation-tables').textContent = result.data.table_ids.map(tid => {
          const table = state.restaurants.find(r => r.id === state.currentSearch.restaurantId)?.tables?.find(t => t.id === tid);
          return table?.label || tid;
        }).join(', ');
        document.getElementById('confirmation').classList.add('visible');

        // Refresh grid
        await search();
      } else if (result.status === 409) {
        document.getElementById('booking-error').textContent = 'Table is no longer available. Please try another.';
        document.getElementById('booking-error').style.display = 'block';
        // Refresh grid
        await search();
      } else {
        document.getElementById('booking-error').textContent = `Booking failed: ${result.data?.error?.code || 'Unknown error'}`;
        document.getElementById('booking-error').style.display = 'block';
      }
    } catch (e) {
      // Network error - show uncertain state
      document.getElementById('booking-uncertain').textContent = 'Unable to confirm your booking. Your reservation may have been created. Please try again.';
      document.getElementById('booking-uncertain').style.display = 'block';
    } finally {
      button.disabled = false;
    }
  };

  const lookupReservation = async () => {
    const ref = document.getElementById('lookup-reference-input').value.trim().toUpperCase();
    if (!ref) return;

    document.getElementById('lookup-error').style.display = 'none';
    document.getElementById('reservation-detail').classList.remove('visible');

    if (!state.token) {
      document.getElementById('lookup-error').textContent = 'Please sign in to look up your booking.';
      document.getElementById('lookup-error').classList.add('visible');
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

        document.getElementById('detail-restaurant').textContent = restaurant.name;
        document.getElementById('detail-tables').textContent = tables;
        document.getElementById('detail-time').textContent = res.starts_at_local.split('T')[1];
        document.getElementById('detail-party').textContent = res.party_size;

        const statusEl = document.getElementById('detail-status');
        statusEl.textContent = res.status;
        statusEl.className = 'reservation-item-value ' + res.status;

        const cancelBtn = document.getElementById('reservation-cancel-button');
        cancelBtn.style.display = res.status === 'confirmed' ? 'block' : 'none';

        document.getElementById('reservation-detail').classList.add('visible');
        document.getElementById('reservation-detail').dataset.ref = ref;
      } else {
        document.getElementById('lookup-error').textContent = 'Booking not found.';
        document.getElementById('lookup-error').classList.add('visible');
      }
    } catch (e) {
      document.getElementById('lookup-error').textContent = 'Error: ' + e.message;
      document.getElementById('lookup-error').classList.add('visible');
    }
  };

  const cancelReservation = async () => {
    const ref = document.getElementById('reservation-detail').dataset.ref;
    if (!ref) return;

    const btn = document.getElementById('reservation-cancel-button');
    btn.disabled = true;

    try {
      const result = await API.cancelReservation(ref);

      if (result.status === 200) {
        const statusEl = document.getElementById('detail-status');
        statusEl.textContent = 'cancelled';
        statusEl.className = 'reservation-item-value cancelled';
        btn.style.display = 'none';
        document.getElementById('lookup-error').textContent = '';
        document.getElementById('lookup-error').classList.remove('visible');
      } else if (result.status === 409) {
        document.getElementById('lookup-error').textContent = 'Cannot cancel: booking is within the cancellation window.';
        document.getElementById('lookup-error').classList.add('visible');
      } else {
        document.getElementById('lookup-error').textContent = 'Cancellation failed.';
        document.getElementById('lookup-error').classList.add('visible');
      }
    } catch (e) {
      document.getElementById('lookup-error').textContent = 'Error: ' + e.message;
      document.getElementById('lookup-error').classList.add('visible');
    } finally {
      btn.disabled = false;
    }
  };

  const submitSignup = async (e) => {
    e.preventDefault();

    const email = document.getElementById('signup-email').value;
    const password = document.getElementById('signup-password').value;
    const displayName = document.getElementById('signup-display-name').value;

    document.getElementById('signup-error').style.display = 'none';

    try {
      const result = await API.signup(email, password, displayName);

      if (result.status === 201) {
        state.token = result.data.token;
        state.displayName = result.data.display_name;
        localStorage.setItem('token', state.token);
        localStorage.setItem('displayName', state.displayName);
        router('/');
      } else {
        document.getElementById('signup-error').textContent = result.data?.error?.message || 'Signup failed';
        document.getElementById('signup-error').style.display = 'block';
      }
    } catch (e) {
      document.getElementById('signup-error').textContent = 'Error: ' + e.message;
      document.getElementById('signup-error').style.display = 'block';
    }
  };

  const submitLogin = async (e) => {
    e.preventDefault();

    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;

    document.getElementById('login-error').style.display = 'none';

    try {
      const result = await API.login(email, password);

      if (result.status === 200) {
        state.token = result.data.token;
        state.displayName = result.data.display_name;
        localStorage.setItem('token', state.token);
        localStorage.setItem('displayName', state.displayName);
        router('/');
      } else {
        document.getElementById('login-error').textContent = result.data?.error?.message || 'Login failed';
        document.getElementById('login-error').style.display = 'block';
      }
    } catch (e) {
      document.getElementById('login-error').textContent = 'Error: ' + e.message;
      document.getElementById('login-error').style.display = 'block';
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

  // Handle back/forward navigation
  window.addEventListener('popstate', render);

  // Initial render
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
