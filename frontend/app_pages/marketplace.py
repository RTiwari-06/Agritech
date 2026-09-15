"""Buyer marketplace & live shopping page."""

import streamlit as st

from .api_helpers import (
    INTENT_COLOR,
    INTENT_MATERIAL_ICON,
    SENTIMENT_COLOR,
    SENTIMENT_MATERIAL_ICON,
    load_active_streams,
    load_products,
    load_recommendations,
    render_chat_message,
    render_live_stream_video,
)


def app():
    st.header("Buyer marketplace & live shopping", icon=":material/store:")

    if "buyer_id" not in st.session_state:
        st.session_state.buyer_id = 1

    buyer_id = st.sidebar.number_input(
        "Your buyer ID",
        min_value=1,
        value=st.session_state.buyer_id,
        step=1,
        key="buyer_id_input",
    )
    st.session_state.buyer_id = buyer_id

    col_search, col_cat, col_sort = st.columns([3, 1, 1])
    with col_search:
        search_query = st.text_input(
            "Search products",
            placeholder="e.g. organic tomatoes, basmati rice...",
            label_visibility="collapsed",
            key="search_query",
        )
    with col_cat:
        category = st.selectbox(
            "Category",
            ["All", "Grain", "Vegetable", "Fruit", "Organic"],
            key="category",
        )
    with col_sort:
        sort_by = st.selectbox(
            "Sort by",
            ["Relevance", "Price: low to high", "Price: high to low", "Rating", "Newest"],
            key="sort_by",
        )

    products = load_products(search_query=search_query if search_query else None, category=category)

    if sort_by == "Price: low to high":
        products.sort(key=lambda p: p.get("dynamic_price", p.get("price", 0)))
    elif sort_by == "Price: high to low":
        products.sort(key=lambda p: p.get("dynamic_price", p.get("price", 0)), reverse=True)
    elif sort_by == "Rating":
        products.sort(key=lambda p: p.get("rating", 0), reverse=True)
    elif sort_by == "Newest":
        products.sort(key=lambda p: p.get("created_at", ""), reverse=True)

    # Live streams
    st.subheader("Live streams", icon=":material/live_tv:")
    active_streams = load_active_streams()

    if active_streams:
        stream_tabs = st.tabs([f"{s['stream_title'][:30]}" for s in active_streams])
        for i, stream in enumerate(active_streams):
            with stream_tabs[i]:
                render_live_stream_view(stream, buyer_id)
    else:
        st.caption("No active live streams at the moment.")

    st.divider()

    # Product catalog
    st.subheader("Product catalog", icon=":material/shopping_bag:")
    if not products:
        st.caption("No products found matching your criteria.")
    else:
        cols_per_row = 2
        for i in range(0, len(products), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, col in enumerate(cols):
                if i + j < len(products):
                    with col:
                        render_product_card(products[i + j], show_buy=True, buyer_id=buyer_id)

    st.divider()

    # Recommendations
    st.subheader("Recommended for you", icon=":material/recommend:")
    recommendations = load_recommendations(buyer_id)

    if recommendations:
        rec_cols = st.columns(min(len(recommendations), 5))
        for i, rec in enumerate(recommendations[:5]):
            with rec_cols[i]:
                st.container(border=True)
                st.markdown(f"**{rec['name']}**")
                st.caption(f"₹{rec.get('dynamic_price', rec.get('price', 0)):.2f}/kg · ⭐ {rec.get('rating', 0):.1f}")
                st.progress(min(rec.get("recommendation_score", 0), 1.0))
                st.caption(f"Match: {rec.get('recommendation_score', 0):.0%}")
    else:
        st.caption("Browse products to get personalized recommendations!")


def render_live_stream_view(stream: dict, buyer_id: int):
    stream_id = stream["id"]

    render_live_stream_video(stream)

    # Chat area — auto-refreshes every 15s via fragment
    @st.fragment(run_every="15s")
    def _render_chat():
        from .api_helpers import api_get, safe_call

        messages_response = safe_call(lambda: api_get(f"/api/streams/{stream_id}/messages", {"limit": 50}))
        messages = messages_response.get("data", []) if messages_response and messages_response.get("ok") else []

        if messages:
            for msg in messages[-20:]:
                render_chat_message(msg)
        else:
            st.caption("No messages yet. Be the first to chat!")

    _render_chat()

    # Chat input
    with st.form(f"chat_form_{stream_id}", clear_on_submit=True):
        col_input, col_send = st.columns([5, 1])
        message_text = col_input.text_input(
            "Message",
            placeholder="Type your message...",
            label_visibility="collapsed",
        )
        send_clicked = col_send.form_submit_button("Send", width="stretch")

        if send_clicked and message_text.strip():
            from .api_helpers import api_post, safe_call

            result = safe_call(
                lambda: api_post(
                    "/api/streams/send_message",
                    {
                        "stream_id": stream_id,
                        "user_id": buyer_id,
                        "message_text": message_text.strip(),
                    },
                )
            )
            if result and result.get("ok"):
                st.rerun()
