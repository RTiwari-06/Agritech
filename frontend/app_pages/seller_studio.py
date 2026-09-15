"""Seller live host studio page."""

import streamlit as st

from .api_helpers import (
    INTENT_COLOR,
    INTENT_MATERIAL_ICON,
    SENTIMENT_COLOR,
    SENTIMENT_MATERIAL_ICON,
    load_inventory,
    load_seller,
    load_seller_streams,
    render_chat_message,
    render_live_stream_video,
)


def app():
    st.header("Seller live host studio", icon=":material/video_call:")

    if "seller_id" not in st.session_state:
        st.session_state.seller_id = 1

    seller_id = st.sidebar.number_input(
        "Your seller ID",
        min_value=1,
        value=st.session_state.seller_id,
        step=1,
        key="seller_id_input",
    )
    st.session_state.seller_id = seller_id

    seller = load_seller(seller_id)
    if not seller:
        st.error("Invalid seller ID. Please select a valid seller account.")
        st.stop()
    if seller.get("role") != "seller":
        st.error("Selected user is not a seller account.")
        st.stop()

    st.subheader("Stream control panel", icon=":material/play_circle:")

    col_stream1, col_stream2 = st.columns(2)

    with col_stream1:
        st.write("**Start a new live stream**")
        stream_title = st.text_input(
            "Stream title",
            placeholder="e.g. Fresh morning harvest from Nashik",
            key="stream_title_input",
        )
        if st.button("Go live", type="primary", width="stretch", key="go_live_btn"):
            if stream_title.strip():
                from .api_helpers import api_post, safe_call

                result = safe_call(
                    lambda: api_post("/api/streams", {"seller_id": seller_id, "stream_title": stream_title.strip()})
                )
                if result and result.get("ok"):
                    st.success(f"Stream started: {result['data']['stream_title']}")
                    st.rerun()
            else:
                st.warning("Please enter a stream title")

    with col_stream2:
        st.write("**Active streams**")
        active_streams = load_seller_streams(seller_id, active_only=True)

        if active_streams:
            for stream in active_streams:
                with st.container():
                    st.write(f"Live: {stream['stream_title']}")
                    if st.button("End stream", key=f"end_{stream['id']}"):
                        from .api_helpers import api_post, safe_call

                        result = safe_call(lambda: api_post(f"/api/streams/{stream['id']}/end", {}))
                        if result and result.get("ok"):
                            st.success("Stream ended")
                            st.rerun()
        else:
            st.caption("No active streams")

    st.divider()

    # Live chat monitor
    st.subheader("Live chat & sentiment monitor", icon=":material/sentiment_satisfied:")

    all_streams = load_seller_streams(seller_id)
    if all_streams:
        selected_stream = st.selectbox(
            "Select stream to monitor",
            options=all_streams,
            format_func=lambda s: f"{'Live' if s['is_active'] else 'Offline'} — {s['stream_title']}",
            key="stream_select",
        )

        if selected_stream:
            stream_id = selected_stream["id"]

            _render_sentiment_monitor(stream_id)

            # Product highlight
            st.divider()
            st.write("**Product highlight**")
            inventory = load_inventory(seller_id)
            seller_products = [item.get("product") for item in inventory if item.get("product")]

            if seller_products:
                highlight_product = st.selectbox(
                    "Select product to highlight in stream",
                    options=seller_products,
                    format_func=lambda p: f"{p['name']} — ₹{p.get('dynamic_price', p.get('price', 0)):.2f}/kg",
                    key="highlight_product",
                )
                if st.button("Highlight product", type="primary"):
                    st.success(f"Highlighted {highlight_product['name']} to live viewers!")
            else:
                st.caption("No products listed yet.")
    else:
        st.caption("No streams found. Start a new stream above.")


def _render_sentiment_monitor(stream_id: int):
    from .api_helpers import api_get, safe_call

    @st.fragment(run_every="15s")
    def _refresh():
        messages_response = safe_call(lambda: api_get(f"/api/streams/{stream_id}/messages", {"limit": 100}))
        messages = messages_response.get("data", []) if messages_response and messages_response.get("ok") else []

        col_sent1, col_sent2, col_sent3 = st.columns(3)
        pos_count = sum(1 for m in messages if m.get("sentiment_label") == "POSITIVE")
        neu_count = sum(1 for m in messages if m.get("sentiment_label") == "NEUTRAL")
        neg_count = sum(1 for m in messages if m.get("sentiment_label") == "NEGATIVE")
        total = len(messages)

        col_sent1.metric("Positive", pos_count, f"{pos_count/total*100:.0f}%" if total else "0%", border=True)
        col_sent2.metric("Neutral", neu_count, f"{neu_count/total*100:.0f}%" if total else "0%", border=True)
        col_sent3.metric("Negative", neg_count, f"{neg_count/total*100:.0f}%" if total else "0%", border=True)

        if total > 0:
            import plotly.graph_objects as go

            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=(pos_count - neg_count) / total * 100,
                title={"text": "Sentiment score"},
                gauge={
                    "axis": {"range": [-100, 100]},
                    "bar": {"color": "darkblue"},
                    "steps": [
                        {"range": [-100, -30], "color": "lightcoral"},
                        {"range": [-30, 30], "color": "lightyellow"},
                        {"range": [30, 100], "color": "lightgreen"},
                    ],
                },
            ))
            fig.update_layout(height=200)
            st.plotly_chart(fig, width="stretch")

        st.write("**Recent messages**")
        for msg in messages[-15:]:
            render_chat_message(msg)

    _refresh()
