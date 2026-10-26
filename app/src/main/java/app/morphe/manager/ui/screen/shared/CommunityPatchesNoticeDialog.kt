/*
 * Copyright 2026 Morphe.
 * https://github.com/MorpheApp/morphe-manager
 */

package app.morphe.manager.ui.screen.shared

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import app.morphe.manager.R
import app.morphe.manager.util.COMMUNITY_PATCHES_URL

/** Opens the community patches website, saying first that nothing it lists is reviewed by Morphe. */
@Composable
fun DialogHostScope.CommunityPatchesNoticeDialog(onDismiss: () -> Unit) {
    val openUrl = rememberOpenUrl()
    ConfirmDialog(
        title = stringResource(R.string.sources_dialog_community_notice_title),
        message = stringResource(R.string.sources_dialog_community_notice_message),
        primaryText = stringResource(R.string.open),
        isPrimaryDestructive = false,
        onDismiss = onDismiss,
        onConfirm = {
            onDismiss()
            openUrl(COMMUNITY_PATCHES_URL)
        }
    )
}
