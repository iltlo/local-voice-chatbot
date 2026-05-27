/**
 * Renders the full conversation history.
 *
 * @param {{ chatHistory: Array<{ id: string, userText: string, assistantText: string, interrupted: boolean }> }} props
 */
export default function ChatHistory({ chatHistory }) {
  return (
    <section className="card history">
      <h2>Chat History</h2>
      {chatHistory.length === 0 ? (
        <p>No previous turns yet.</p>
      ) : (
        <div className="history-list">
          {chatHistory.map((turn, idx) => (
            <article key={turn.id || idx} className="history-turn">
              <p className="history-you">You: {turn.userText || "..."}</p>
              <p className="history-assistant">
                Assistant: {turn.assistantText || (turn.interrupted ? "[interrupted]" : "...")}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
