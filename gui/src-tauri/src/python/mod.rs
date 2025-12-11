//! Python bridge module
//!
//! Manages communication with Python backend via JSON-RPC 2.0 over stdio.

mod bridge;
mod protocol;

pub use bridge::PythonBridge;
pub use protocol::JsonRpcResponse;
