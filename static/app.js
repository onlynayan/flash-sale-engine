const catalogDiv = document.getElementById("catalog");

// Toast configuration for beautiful, non-intrusive alerts (Dark mode)
const Toast = Swal.mixin({
    toast: true,
    position: 'top-end',
    showConfirmButton: false,
    timer: 3000,
    timerProgressBar: true,
    background: '#161b22',
    color: '#c9d1d9',
    iconColor: '#58a6ff'
});

let currentPage = 1;
const ITEMS_PER_PAGE = 12;

// 1. Fetch the exact page catalog from our FastAPI backend
async function loadCatalog(page = 1) {
    try {
        const skip = (page - 1) * ITEMS_PER_PAGE;
        const response = await fetch(`/products/?skip=${skip}&limit=${ITEMS_PER_PAGE}`);
        const data = await response.json();
        
        const products = data.items;
        
        catalogDiv.innerHTML = ""; // Clear loading text
        
        products.forEach((product, index) => {
            const isOutOfStock = product.stock <= 0;
            const stockClass = isOutOfStock ? "stock-count out-of-stock" : "stock-count";
            const stockText = isOutOfStock ? "SOLD OUT" : product.stock;
            const buttonState = isOutOfStock ? "disabled" : "";
            const buttonText = isOutOfStock ? "Out of Stock" : "Add to Cart";

            const cardHTML = `
                <div class="product-card" style="animation-delay: ${index * 0.1}s">
                    <h2>${product.name}</h2>
                    <p class="price">$${(product.price / 100).toFixed(2)}</p>
                    <div class="stock-container">
                        <span class="stock-label">Available Stock</span>
                        <span id="stock-display-${product.id}" class="${stockClass}">${stockText}</span>
                    </div>
                    <button id="buy-button-${product.id}" onclick="addToCart(${product.id}, '${product.name.replace(/'/g, "\\'")}', ${product.price})" ${buttonState}>${buttonText}</button>
                </div>
            `;
            catalogDiv.innerHTML += cardHTML;
        });
        
        // Render the Pagination Buttons using the Server's Math
        renderPagination(data.page, data.pages);
        
    } catch (e) {
        catalogDiv.innerHTML = "<div class='loader-container'>Failed to load catalog. Please refresh.</div>";
        console.error("Catalog Error: ", e);
    }
}

function renderPagination(currentPage, totalPages) {
    const container = document.getElementById("pagination-controls");
    if (!container || totalPages <= 1) {
        if (container) container.innerHTML = "";
        return;
    }

    // Build the window of pages around current page
    const WINDOW = 2; // pages to show on each side of current
    let pages = new Set();

    // Always include first and last
    pages.add(1);
    pages.add(totalPages);

    // Add window around current page
    for (let i = Math.max(1, currentPage - WINDOW); i <= Math.min(totalPages, currentPage + WINDOW); i++) {
        pages.add(i);
    }

    // Sort the page set into an array
    const sorted = Array.from(pages).sort((a, b) => a - b);

    let html = "";

    // Prev button
    html += `<button class="page-btn" ${currentPage <= 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})">&#8592; Prev</button>`;

    // Page number buttons with ellipsis gaps
    let prev = 0;
    for (const page of sorted) {
        if (prev && page - prev > 1) {
            html += `<span class="page-ellipsis">…</span>`;
        }
        html += `<button class="page-btn ${page === currentPage ? 'active' : ''}" onclick="changePage(${page})">${page}</button>`;
        prev = page;
    }

    // Next button
    html += `<button class="page-btn" ${currentPage >= totalPages ? 'disabled' : ''} onclick="changePage(${currentPage + 1})">Next &#8594;</button>`;

    container.innerHTML = html;
}


function changePage(newPage) {
    currentPage = newPage;
    catalogDiv.innerHTML = "<div class='loader-container'><div class='spinner'></div>Loading...</div>";
    loadCatalog(currentPage);
    // Smoothly scroll back to the top of the grid
    document.querySelector('.overlap-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// 2. Open a robust GLOBAL WebSocket Connection
let ws;
function connectWebSocket() {
    // Use the exact host the user is visiting to prevent protocol/CORS errors
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/catalog`);

    ws.onopen = () => {
        console.log("🟢 Connected to live catalog updates!");
    };

    ws.onmessage = function(event) {
        const data = JSON.parse(event.data);
        
        const stockDisplay = document.getElementById(`stock-display-${data.product_id}`);
        const buyButton = document.getElementById(`buy-button-${data.product_id}`);
        
        if (!stockDisplay || !buyButton) return;

        stockDisplay.innerText = data.stock;

        // Apply visual bounce effect when stock updates
        stockDisplay.style.transform = 'scale(1.2)';
        setTimeout(() => stockDisplay.style.transform = 'scale(1)', 200);

        // Lock it down immediately if empty
        if (data.stock <= 0) {
            stockDisplay.innerText = "SOLD OUT";
            stockDisplay.className = "stock-count out-of-stock";
            buyButton.disabled = true;
            // Only change text if it's not currently saying "Processing"
            if (!buyButton.innerText.includes("Processing")) {
                buyButton.innerText = "Out of Stock";
            }
        } else {
            stockDisplay.className = "stock-count";
            if (!buyButton.innerText.includes("Processing")) {
                buyButton.disabled = false;
                buyButton.innerText = "Add to Cart";
            }
        }
    };

    ws.onclose = () => {
        console.log("🔴 WebSocket disconnected. Reconnecting in 2 seconds...");
        setTimeout(connectWebSocket, 2000); // Auto-reconnect!
    };
}

// ---------------------------------------------------------
// NEW: CART LOGIC (Phase 9)
// ---------------------------------------------------------
function toggleCart() {
    document.getElementById("cart-drawer").classList.toggle("open");
}

let cartItems = []; // Array of objects: {id, name, price}
let cartId = null;
let timerStarted = false; // Guards startCartTimer from running more than once!
let timeRemaining = 40;
let timerInterval = null;

// Ensure browsers without crypto (http) have a fallback
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

async function addToCart(productId, productName, productPrice) {
    // PREVENT RACE CONDITIONS: Synchronously generate the cartId on the very first click.
    // All parallel inflight requests will instantly share this same ID.
    if (!cartId) {
        cartId = window.crypto && crypto.randomUUID ? crypto.randomUUID() : generateUUID();
    }
    const buyButton = document.getElementById(`buy-button-${productId}`);
    buyButton.disabled = true;
    buyButton.innerText = "⏳ Adding...";
    
    try {
        const response = await fetch('/cart/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                "user_id": 1, 
                "product_id": productId,
                "cart_id": cartId
            })
        });
        
        const responseData = await response.json();

        if (response.ok) {
            // IF SUCCESS: The Bouncer approved the reservation
            
            // Add to UI
            cartItems.push({ id: productId, name: productName, price: productPrice });
            updateCartUI();
            
            // Start the timer only once, after the FIRST confirmed successful add!
            if (!timerStarted) {
                timerStarted = true;
                startCartTimer();
            }
            
            // Pop the drawer open and update badge!
            document.getElementById("cart-drawer").classList.add("open");
            document.getElementById("cart-count-badge").innerText = cartItems.length;
            
            Toast.fire({icon: 'success', title: 'Added to Cart!'});
        } else {
            Toast.fire({icon: 'error', title: responseData.detail});
        }
    } catch (e) {
        console.error("Cart error", e);
        Toast.fire({icon: 'error', title: 'Network Error!'});
    }
    
    // Always reset button if it wasn't sold out
    const currentStock = parseInt(document.getElementById(`stock-display-${productId}`).innerText);
    if (!isNaN(currentStock) && currentStock > 0) {
        buyButton.disabled = false;
        buyButton.innerText = "Add to Cart";
    } else {
        buyButton.innerText = "Out of Stock";
    }
}

function updateCartUI() {
    const cartContainer = document.getElementById("cart-items");
    const checkoutBtn = document.getElementById("checkout-btn");
    
    if (cartItems.length === 0) {
        cartContainer.innerHTML = `<div class="empty-cart-msg">Your cart is empty.</div>`;
        checkoutBtn.style.display = "none";
        return;
    }
    
    // Show checkout button
    checkoutBtn.style.display = "block";
    
    // Render list and total
    let totalCents = 0;
    let html = "";
    cartItems.forEach((item, index) => {
        html += `
            <div class="cart-item">
                <span class="remove-btn" onclick="removeFromCart(${index}, ${item.id})" title="Remove item">❌</span>
                <span style="flex:1; padding-left: 10px;">${item.name}</span>
                <span>$${(item.price / 100).toFixed(2)}</span>
            </div>
        `;
        totalCents += item.price;
    });
    
    html += `
        <div class="cart-item" style="border-top: 1px solid rgba(255,255,255,0.2); font-weight: bold; margin-top: 15px;">
            <span>TOTAL</span>
            <span style="color: var(--success);">$${(totalCents / 100).toFixed(2)}</span>
        </div>
    `;
    
    cartContainer.innerHTML = html;
}

function startCartTimer() {
    const timerDisplay = document.getElementById("timer-display");
    const timeLeftSpan = document.getElementById("time-left");
    
    timerDisplay.className = "timer-active";
    timeLeftSpan.innerText = `00:${timeRemaining < 10 ? '0' : ''}${timeRemaining}`;
    
    timerInterval = setInterval(() => {
        timeRemaining--;
        timeLeftSpan.innerText = `00:${timeRemaining < 10 ? '0' : ''}${timeRemaining}`;
        
        if (timeRemaining <= 0) {
            // Cart Expired!
            clearInterval(timerInterval);
            timerDisplay.className = "timer-hidden";
            cartItems = [];
            cartId = null;
            timerStarted = false;
            timeRemaining = 40;
            updateCartUI();
            document.getElementById("cart-count-badge").innerText = "0";
            
            Swal.fire({
                title: 'Time Expired!',
                text: 'You ran out of time! Your cart was emptied and items returned to stock.',
                icon: 'warning',
                background: '#161b22',
                color: '#c9d1d9',
                confirmButtonColor: '#58a6ff'
            });
        }
    }, 1000);
}

// NEW: Remove an individual item from the cart
async function removeFromCart(arrayIndex, productId) {
    if (!cartId) return;
    
    // Optimistically remove from UI to feel fast
    const removedItem = cartItems.splice(arrayIndex, 1)[0];
    updateCartUI();
    document.getElementById("cart-count-badge").innerText = cartItems.length;

    try {
        const response = await fetch('/cart/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                "user_id": 1, 
                "product_id": productId,
                "cart_id": cartId
            })
        });
        
        const responseData = await response.json();

        if (response.ok) {
            Toast.fire({icon: 'success', title: 'Item Removed'});
            
            // If that was the last item, kill the timer entirely
            if (cartItems.length === 0) {
                clearInterval(timerInterval);
                document.getElementById("timer-display").className = "timer-hidden";
                cartId = null;
                timerStarted = false;
                timeRemaining = 40; // Reset for next time
            }
        } else {
            // If it failed (maybe expired while clicking), rollback the UI
            cartItems.splice(arrayIndex, 0, removedItem);
            updateCartUI();
            document.getElementById("cart-count-badge").innerText = cartItems.length;
            Toast.fire({icon: 'error', title: responseData.detail});
        }
    } catch (e) {
        console.error("Remove error", e);
        // Rollback UI
        cartItems.splice(arrayIndex, 0, removedItem);
        updateCartUI();
        document.getElementById("cart-count-badge").innerText = cartItems.length;
        Toast.fire({icon: 'error', title: 'Network Error!'});
    }
}

// 5. BULK CHECKOUT
async function checkoutCart() {
    if (cartItems.length === 0 || !cartId) return;
    
    const checkoutBtn = document.getElementById("checkout-btn");
    checkoutBtn.disabled = true;
    checkoutBtn.innerText = "Processing...";
    
    // Pause the visual timer, they hit the button!
    clearInterval(timerInterval);

    try {
        const response = await fetch('/cart/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                "user_id": 1, 
                "cart_id": cartId
            })
        });
        
        const responseData = await response.json();

        if (response.ok) {
            Swal.fire({
                title: 'Order Successful!',
                text: 'Your bulk order was confirmed and sent to the worker!',
                icon: 'success',
                background: '#161b22',
                color: '#c9d1d9',
                confirmButtonColor: '#2ea043'
            });
            
            // Reset the cart happily!
            document.getElementById("timer-display").className = "timer-hidden";
            cartItems = [];
            cartId = null;
            timerStarted = false;
            timeRemaining = 40;
            updateCartUI();
            
            // FIX: Re-enable the button for the next order!
            checkoutBtn.disabled = false;
            checkoutBtn.innerText = "Complete Payment";
            
            document.getElementById("cart-count-badge").innerText = "0";
            document.getElementById("cart-drawer").classList.remove("open");
        } else {
            // Could be expired before they clicked
            Swal.fire({
                title: 'Checkout Failed',
                text: responseData.detail,
                icon: 'error',
                background: '#161b22',
                color: '#c9d1d9',
                confirmButtonColor: '#f85149'
            });
            // Don't auto-reset cart here, let the interval handle it if it actually expired, or user checks error.
            checkoutBtn.disabled = false;
            checkoutBtn.innerText = "Complete Payment";
        }
    } catch (e) {
        console.error("Checkout error", e);
        Toast.fire({icon: 'error', title: 'Network Error!'});
        checkoutBtn.disabled = false;
        checkoutBtn.innerText = "Complete Payment";
    }
}

// Initialize the page!
loadCatalog();
connectWebSocket();
