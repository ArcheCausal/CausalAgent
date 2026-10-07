import { createApp } from "vue";
import "@causalagent/design-system/styles.css";
import App from "./App.vue";
import "../../../../packages/design-system/src/styles/fonts.css";
import "../../../../packages/design-system/src/styles/tokens/typography.css";
import "./style.css";
import "./shared-design-system.css";
import "./rag-eval-quality.css";

createApp(App).mount("#app");
