# مصفوفة صلاحيات BranchWise

تعرّف `permissions.py` صلاحيات الأدوار الثلاثة، وتتحقق من منح العملية ونطاق النشاط والفرع. كل عملية مرفوضة افتراضيًا ما لم تكن مدرجة للدور وموجودة أيضًا في مجموعة المنح الموثوقة للمستخدم.

## المصفوفة المعتمدة

### المالك (`Owner`)

- `dashboard:view`, `products:view`
- `users:manage`, `branches:manage`, `business_settings:manage`
- `product_catalog:manage`, `product_catalog:approve`
- `inventory:view`, `inventory:adjust`
- `sales:record`, `sales:view`, `sale_cancellation:approve`
- `suppliers:view`, `suppliers:manage`, `purchases:manage`
- `transfers:request`, `transfers:approve_source`, `transfers:dispatch`, `transfers:confirm_receipt`
- `returns:record`, `returns:inspect`
- `reports:view_business`
- `ai:recommendations`, `ai:assistant`, `ai:decision_log`
- `password:change_self`

### مدير الفرع (`Branch Manager`)

- `dashboard:view`, `products:view`
- `product_request:review`, `product_request:comment`
- `inventory:view`, `inventory:adjust`
- `sales:record`, `sales:view`, `sale_cancellation:approve`
- `suppliers:view`, `purchases:manage_authorized`
- `transfers:request`, `transfers:approve_source`, `transfers:dispatch`, `transfers:confirm_receipt`
- `returns:record`, `returns:inspect`
- `reports:view_branch`
- `ai:recommendations`, `ai:assistant`, `ai:decision_log`
- `password:change_self`

### الموظف (`Employee`)

- `dashboard:view`, `products:view`, `inventory:view`
- `sales:record`, `sales:view_authorized`
- `product_request:submit`, `sale_cancellation:request`
- `returns:record`
- `password:change_self`

## قواعد النطاق والثقة

- لا يصل أي دور إلى نشاط تجاري غير نشاطه. يستطيع المالك الوصول إلى موارد جميع فروع نشاطه، بما فيها الموارد على مستوى النشاط التي لا ترتبط بفرع.
- يقتصر مدير الفرع والموظف على الفرع المعيّن لهما. على العملية تمرير فرع المورد المحمّل من الخادم؛ في التحويل يكون فرع المصدر عند الاعتماد أو الإرسال، وفرع الوجهة عند تأكيد الاستلام.
- `ROLE_PERMISSIONS` هي الحد الأعلى لصلاحيات كل دور. لا تستطيع مجموعة `AuthenticatedPrincipal.permissions` إضافة صلاحية غير موجودة في مصفوفة الدور، لكنها تستطيع حجب صلاحيات إضافية. يجب أن تأتي هذه المجموعة من سياسة أو بيانات موثوقة على الخادم.
- يتطلب `purchases:manage_authorized` منحًا موثوقًا لمدير الفرع، كما يتطلب عرض سجل المبيعات المصرّح به تصفية سجلات المبيعات على الخادم. فحص الدور والنطاق وحده لا يحدد السجلات المسموح بها داخل الفرع.
- يجب إنشاء `AuthenticatedPrincipal` من جلسة مصادق عليها وبيانات المستخدم الموثوقة، وإنشاء `ResourceScope` من المورد الذي حمّله الخادم. لا تستخدم قيم الدور أو النشاط أو الفرع أو الصلاحية الواردة من الطلب لإثبات الهوية أو النطاق.
- هذا الملف وهذا الفحص لا يضيفان مصادقة أو اتصالًا بقاعدة البيانات. واجهات API الحالية لم تتغير.
