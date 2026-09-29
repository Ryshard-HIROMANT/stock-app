import os
import json
from flask import Flask, render_template, redirect, url_for, flash, request, send_file
from config import Config
from models import db, User, Material, Batch, Location, Transaction, Category, Transfer, TransferItem, TransferRequest, TransferRequestItem, SpendingDraft, Revision, OPERATING_ROOM_LOCATION_CODE
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import date, datetime
from io import BytesIO
from urllib.parse import urlsplit
from flask_wtf.csrf import CSRFProtect
from schema_migrations import migrate_request_item_schema

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите для доступа.'

csrf = CSRFProtect()


def is_safe_redirect_target(target):
    """Allow redirects only to local paths."""
    if not target:
        return False
    parsed = urlsplit(target)
    return not parsed.scheme and not parsed.netloc and target.startswith('/') and not target.startswith('//')


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        migrate_request_item_schema(db.engine)
        db.create_all()

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))


    @app.context_processor
    def inject_pending_transfer_requests():
        pending_count = 0
        if current_user.is_authenticated and current_user.can_process_requests():
            pending_count = TransferRequest.query.filter_by(status='pending').count()
        return {'pending_transfer_requests_count': pending_count}

    # ──────────────────────────────────────────
    # АВТОРИЗАЦИЯ
    # ──────────────────────────────────────────
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                if user.is_active:
                    login_user(user)
                    flash(f'Добро пожаловать, {user.full_name or user.username}!', 'success')
                    next_page = request.args.get('next', '')
                    if is_safe_redirect_target(next_page):
                        return redirect(next_page)
                    return redirect(url_for('index'))
                else:
                    flash('Ваша учётная запись отключена.', 'danger')
            else:
                flash('Неверное имя пользователя или пароль.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Вы вышли из системы.', 'info')
        return redirect(url_for('login'))

    # ──────────────────────────────────────────
    # ГЛАВНАЯ СТРАНИЦА (дашборд)
    # ──────────────────────────────────────────
    @app.route('/')
    @login_required
    def index():
        if not current_user.can_view_dashboard():
            return redirect(url_for('locations'))
        today = date.today()
        category_id = request.args.get('category_id', '').strip()

        query = Material.query
        if category_id:
            query = query.filter(Material.category_id == int(category_id))
        materials_all = query.order_by(Material.name).all()

        aggregated = []
        for m in materials_all:
            active_batches = [b for b in m.batches if b.is_active and b.remaining > 0]
            if not active_batches:
                continue
            total_remaining = sum(b.remaining for b in active_batches)
            expiry_dates = [b.expiry_date for b in active_batches if b.expiry_date]
            nearest_expiry = min(expiry_dates) if expiry_dates else None
            has_expired = any(b.is_expired for b in active_batches)
            low_stock = total_remaining <= (m.min_stock or 0)
            aggregated.append({
                'material': m,
                'total_remaining': total_remaining,
                'nearest_expiry': nearest_expiry,
                'has_expired': has_expired,
                'low_stock': low_stock,
                'min_stock': m.min_stock or 0,
            })

        categories_list = Category.query.order_by(Category.name).all()

        total_materials = Material.query.count()
        total_batches = Batch.query.filter(Batch.is_active == True).count()
        expired_batches = Batch.query.filter(
            Batch.is_active == True, Batch.quantity - Batch.used > 0,
            Batch.expiry_date < today
        ).count()
        expiring_soon = Batch.query.filter(
            Batch.is_active == True, Batch.quantity - Batch.used > 0,
            Batch.expiry_date >= today,
            Batch.expiry_date <= db.func.date(today, '+90 days')
        ).count()
        reusable_count = Material.query.filter_by(is_reusable=True).count()
        pending_requests = TransferRequest.query.filter_by(status='pending').count()
        pending_drafts = SpendingDraft.query.filter_by(status='pending').count()

        return render_template('index.html',
                               aggregated=aggregated,
                               categories=categories_list,
                               selected_category=category_id,
                               total_materials=total_materials,
                               total_batches=total_batches,
                               expired_batches=expired_batches,
                               expiring_soon=expiring_soon,
                               reusable_count=reusable_count,
                               pending_requests=pending_requests,
                               pending_drafts=pending_drafts,
                               today=today)

    # ──────────────────────────────────────────
    # ДЕТАЛИЗАЦИЯ
    # ──────────────────────────────────────────
    @app.route('/dashboard/expired')
    @login_required
    def dashboard_expired():
        if not current_user.can_view_dashboard():
            return redirect(url_for('locations'))
        today = date.today()
        batches = Batch.query.filter(
            Batch.is_active == True, Batch.quantity - Batch.used > 0,
            Batch.expiry_date < today
        ).order_by(Batch.expiry_date.asc()).all()
        return render_template('dashboard_filtered.html', batches=batches,
                               title='Просроченные позиции', filter_type='expired')

    @app.route('/dashboard/expiring')
    @login_required
    def dashboard_expiring():
        if not current_user.can_view_dashboard():
            return redirect(url_for('locations'))
        today = date.today()
        batches = Batch.query.filter(
            Batch.is_active == True, Batch.quantity - Batch.used > 0,
            Batch.expiry_date >= today,
            Batch.expiry_date <= db.func.date(today, '+90 days')
        ).order_by(Batch.expiry_date.asc()).all()
        return render_template('dashboard_filtered.html', batches=batches,
                               title='Истекают в течение 90 дней', filter_type='expiring')

    @app.route('/dashboard/low-stock')
    @login_required
    def dashboard_low_stock():
        if not current_user.can_view_low_stock():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        materials_all = Material.query.order_by(Material.name).all()
        aggregated = []
        for m in materials_all:
            active_batches = [b for b in m.batches if b.is_active and b.remaining > 0]
            total_remaining = sum(b.remaining for b in active_batches)
            min_stock = m.min_stock or 0
            if total_remaining <= min_stock:
                aggregated.append({
                    'material': m,
                    'total_remaining': total_remaining,
                    'min_stock': min_stock,
                    'deficit': min_stock - total_remaining,
                })

        return render_template('low_stock.html', aggregated=aggregated)

    # ──────────────────────────────────────────
    # МАТЕРИАЛЫ
    # ──────────────────────────────────────────
    @app.route('/materials')
    @login_required
    def materials():
        if not current_user.can_view_catalog():
            return redirect(url_for('locations'))
        search = request.args.get('search', '').strip()
        category_id = request.args.get('category_id', '').strip()
        reusable = request.args.get('reusable', '').strip()
        query = Material.query
        if search:
            query = query.filter(
                Material.name.ilike(f'%{search}%') |
                Material.size.ilike(f'%{search}%') |
                Material.barcode.ilike(f'%{search}%')
            )
        if category_id:
            query = query.filter(Material.category_id == int(category_id))
        if reusable == '1':
            query = query.filter(Material.is_reusable == True)
        elif reusable == '0':
            query = query.filter(Material.is_reusable == False)
        materials_list = query.order_by(Material.name).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('materials.html', materials=materials_list, search=search,
                               categories=categories_list, selected_category=category_id)

    @app.route('/materials/add', methods=['GET', 'POST'])
    @login_required
    def material_add():
        if not current_user.is_admin():
            flash('Изменять справочник может только администратор.', 'danger')
            return redirect(url_for('materials'))
        categories_list = Category.query.order_by(Category.name).all()
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            size = request.form.get('size', '').strip()
            barcode = request.form.get('barcode', '').strip()
            category_id = request.form.get('category_id', '').strip()
            is_reusable = request.form.get('is_reusable') == '1'
            min_stock = request.form.get('min_stock', '0').strip()
            note = request.form.get('note', '').strip()
            if not name:
                flash('Название обязательно!', 'danger')
                return render_template('material_form.html', material=None,
                                       categories=categories_list, prefill_barcode=barcode)
            if not barcode:
                flash('QR-код обязателен! Материал нельзя создать без QR-кода.', 'danger')
                return render_template('material_form.html', material=None,
                                       categories=categories_list, prefill_barcode=barcode)
            if Material.query.filter_by(barcode=barcode).first():
                flash(f'Материал с QR-кодом "{barcode}" уже существует!', 'danger')
                return render_template('material_form.html', material=None,
                                       categories=categories_list, prefill_barcode=barcode)
            material = Material(
                name=name, size=size if size else None,
                barcode=barcode,
                category_id=int(category_id) if category_id else None,
                is_reusable=is_reusable,
                min_stock=int(min_stock) if min_stock.isdigit() else 0,
                note=note if note else None
            )
            db.session.add(material)
            db.session.commit()
            flash(f'Материал "{name}" добавлен!', 'success')
            return redirect(url_for('materials'))
        prefill_barcode = request.args.get('barcode', '').strip()
        return render_template('material_form.html', material=None,
                               categories=categories_list, prefill_barcode=prefill_barcode)

    @app.route('/materials/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def material_edit(id):
        if not current_user.is_admin():
            flash('Изменять справочник может только администратор.', 'danger')
            return redirect(url_for('materials'))
        material = Material.query.get_or_404(id)
        categories_list = Category.query.order_by(Category.name).all()
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            barcode = request.form.get('barcode', '').strip()
            if not barcode:
                flash('QR-код обязателен!', 'danger')
                return render_template('material_form.html', material=material,
                                       categories=categories_list, prefill_barcode=barcode)
            existing = Material.query.filter(Material.barcode == barcode, Material.id != id).first()
            if existing:
                flash(f'QR-код "{barcode}" уже используется материалом "{existing.name}"!', 'danger')
                return render_template('material_form.html', material=material,
                                       categories=categories_list, prefill_barcode=barcode)
            material.name = name
            material.size = request.form.get('size', '').strip() or None
            material.barcode = barcode
            cat_id = request.form.get('category_id', '').strip()
            material.category_id = int(cat_id) if cat_id else None
            material.is_reusable = request.form.get('is_reusable') == '1'
            min_stock = request.form.get('min_stock', '0').strip()
            material.min_stock = int(min_stock) if min_stock.isdigit() else 0
            material.note = request.form.get('note', '').strip() or None
            db.session.commit()
            flash(f'Материал "{material.name}" обновлён!', 'success')
            return redirect(url_for('materials'))
        return render_template('material_form.html', material=material, categories=categories_list)

    @app.route('/materials/<int:id>/delete', methods=['POST'])
    @login_required
    def material_delete(id):
        if not current_user.is_admin():
            flash('Только администратор.', 'danger')
            return redirect(url_for('materials'))
        material = Material.query.get_or_404(id)
        name = material.name
        for batch in material.batches:
            Transaction.query.filter_by(batch_id=batch.id).delete()
        Batch.query.filter_by(material_id=material.id).delete()
        db.session.delete(material)
        db.session.commit()
        flash(f'Материал "{name}" удалён.', 'info')
        return redirect(url_for('materials'))

    # ──────────────────────────────────────────
    # КАТЕГОРИИ
    # ──────────────────────────────────────────
    @app.route('/categories')
    @login_required
    def categories():
        if not current_user.can_view_catalog():
            return redirect(url_for('locations'))
        cats = Category.query.order_by(Category.name).all()
        return render_template('categories.html', categories=cats)

    @app.route('/categories/add', methods=['GET', 'POST'])
    @login_required
    def category_add():
        if not current_user.is_admin():
            flash('Изменять справочник может только администратор.', 'danger')
            return redirect(url_for('categories'))
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            if not name:
                flash('Название обязательно!', 'danger')
                return render_template('category_form.html', category=None)
            if Category.query.filter_by(name=name).first():
                flash(f'Категория "{name}" уже существует!', 'danger')
                return render_template('category_form.html', category=None)
            cat = Category(name=name, description=description if description else None)
            db.session.add(cat)
            db.session.commit()
            flash(f'Категория "{name}" добавлена!', 'success')
            return redirect(url_for('categories'))
        return render_template('category_form.html', category=None)

    @app.route('/categories/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def category_edit(id):
        if not current_user.is_admin():
            flash('Изменять справочник может только администратор.', 'danger')
            return redirect(url_for('categories'))
        cat = Category.query.get_or_404(id)
        if request.method == 'POST':
            new_name = request.form.get('name', '').strip()
            existing = Category.query.filter(Category.name == new_name, Category.id != id).first()
            if existing:
                flash(f'Категория "{new_name}" уже существует!', 'danger')
                return render_template('category_form.html', category=cat)
            cat.name = new_name
            cat.description = request.form.get('description', '').strip() or None
            db.session.commit()
            flash(f'Категория "{cat.name}" обновлена!', 'success')
            return redirect(url_for('categories'))
        return render_template('category_form.html', category=cat)

    @app.route('/categories/<int:id>/delete', methods=['POST'])
    @login_required
    def category_delete(id):
        if not current_user.is_admin():
            flash('Только администратор.', 'danger')
            return redirect(url_for('categories'))
        cat = Category.query.get_or_404(id)
        Material.query.filter_by(category_id=cat.id).update({Material.category_id: None})
        db.session.delete(cat)
        db.session.commit()
        flash(f'Категория "{cat.name}" удалена.', 'info')
        return redirect(url_for('categories'))

    # ──────────────────────────────────────────
    # ЛОКАЦИИ
    # ──────────────────────────────────────────
    @app.route('/locations')
    @login_required
    def locations():
        locations_query = Location.query
        if current_user.role == 'xray_lab':
            locations_query = locations_query.filter_by(code=OPERATING_ROOM_LOCATION_CODE)
        locs = locations_query.order_by(Location.code).all()
        return render_template('locations.html', locations=locs)

    @app.route('/locations/add', methods=['GET', 'POST'])
    @login_required
    def location_add():
        if not current_user.is_admin():
            flash('Изменять справочник может только администратор.', 'danger')
            return redirect(url_for('locations'))
        if request.method == 'POST':
            code = request.form.get('code', '').strip()
            description = request.form.get('description', '').strip()
            if not code:
                flash('Код локации обязателен!', 'danger')
                return render_template('location_form.html', location=None)
            if Location.query.filter_by(code=code).first():
                flash(f'Локация "{code}" уже существует!', 'danger')
                return render_template('location_form.html', location=None)
            loc = Location(code=code, description=description if description else None)
            db.session.add(loc)
            db.session.commit()
            flash(f'Локация "{code}" добавлена!', 'success')
            return redirect(url_for('locations'))
        return render_template('location_form.html', location=None)

    @app.route('/locations/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def location_edit(id):
        if not current_user.is_admin():
            flash('Изменять справочник может только администратор.', 'danger')
            return redirect(url_for('locations'))
        loc = Location.query.get_or_404(id)
        if request.method == 'POST':
            new_code = request.form.get('code', '').strip()
            existing = Location.query.filter(Location.code == new_code, Location.id != id).first()
            if existing:
                flash(f'Локация "{new_code}" уже существует!', 'danger')
                return render_template('location_form.html', location=loc)
            loc.code = new_code
            loc.description = request.form.get('description', '').strip() or None
            db.session.commit()
            flash(f'Локация "{loc.code}" обновлена!', 'success')
            return redirect(url_for('locations'))
        return render_template('location_form.html', location=loc)

    @app.route('/locations/<int:id>/delete', methods=['POST'])
    @login_required
    def location_delete(id):
        if not current_user.is_admin():
            flash('Только администратор.', 'danger')
            return redirect(url_for('locations'))
        loc = Location.query.get_or_404(id)
        Batch.query.filter_by(location_id=loc.id).update({Batch.location_id: None})
        db.session.delete(loc)
        db.session.commit()
        flash(f'Локация "{loc.code}" удалена.', 'info')
        return redirect(url_for('locations'))

    @app.route('/locations/<int:id>/view')
    @login_required
    def location_view(id):
        loc = Location.query.get_or_404(id)
        if not current_user.can_view_location_contents(loc.code):
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('locations'))
        batches = Batch.query.filter(
            Batch.location_id == loc.id,
            Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()
        return render_template('location_view.html', location=loc, batches=batches)

    # ──────────────────────────────────────────
    # СКАНЕР
    # ──────────────────────────────────────────
    @app.route('/scanner')
    @login_required
    def scanner():
        if not current_user.can_use_scanner():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('locations'))
        return render_template('scanner.html')

    @app.route('/api/material-by-barcode')
    @login_required
    def api_material_by_barcode():
        if not current_user.can_use_scanner():
            return {'found': False}, 403
        import re

        barcode = request.args.get('barcode', '').strip()
        if not barcode:
            return {'found': False}

        def normalize(s):
            # Убираем ВСЕ пробельные символы (пробелы, переносы, табы) и регистр
            return re.sub(r'\s+', '', s or '').lower()

        norm_scanned = normalize(barcode)

        # 1. Точное совпадение
        material = Material.query.filter_by(barcode=barcode).first()

        # 2. Сравнение без учёта пробелов и регистра
        if not material:
            for m in Material.query.all():
                if m.barcode and normalize(m.barcode) == norm_scanned:
                    material = m
                    break

        if not material:
            return {'found': False}

        batches = Batch.query.filter(
            Batch.material_id == material.id, Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()
        total_remaining = sum(b.remaining for b in batches)
        return {
            'found': True,
            'material': {
                'id': material.id, 'name': material.name, 'size': material.size,
                'barcode': material.barcode, 'category_id': material.category_id,
                'category_name': material.category_name, 'is_reusable': material.is_reusable,
            },
            'total_remaining': total_remaining,
        }

    @app.route('/api/categories')
    @login_required
    def api_categories():
        categories_query = Category.query
        if current_user.role == 'xray_lab':
            oper_location = Location.query.filter_by(code=OPERATING_ROOM_LOCATION_CODE).first()
            if not oper_location:
                return []
            category_ids = db.session.query(Material.category_id).join(
                Batch, Batch.material_id == Material.id
            ).filter(
                Batch.location_id == oper_location.id,
                Batch.is_active == True,
                Batch.quantity - Batch.used > 0,
                Material.category_id.isnot(None)
            ).distinct()
            categories_query = categories_query.filter(Category.id.in_(category_ids))
        cats = categories_query.order_by(Category.name).all()
        return [{'id': c.id, 'name': c.name} for c in cats]

    # ──────────────────────────────────────────
    # ПРИХОД
    # ──────────────────────────────────────────
    @app.route('/operations/in', methods=['GET', 'POST'])
    @login_required
    def operation_in():
        if not current_user.is_storekeeper():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        if request.method == 'POST':
            mat_id = request.form.get('material_id', '').strip()
            qty = int(request.form.get('quantity', 0))
            expiry_str = request.form.get('expiry_date', '').strip()
            location_code = request.form.get('location_code', '').strip()
            batch_number = request.form.get('batch_number', '').strip()
            note = request.form.get('note', '').strip()
            new_name = request.form.get('new_material_name', '').strip()
            new_size = request.form.get('new_material_size', '').strip()
            new_barcode = request.form.get('new_material_barcode', '').strip()
            new_category_id = request.form.get('new_material_category_id', '').strip()
            is_reusable = request.form.get('is_reusable') == '1'
            if qty <= 0:
                flash('Укажите количество больше 0.', 'danger')
                return redirect(url_for('operation_in'))
            if mat_id and mat_id != 'new':
                material = Material.query.get_or_404(int(mat_id))
            elif new_name:
                if not new_barcode:
                    flash('QR-код обязателен для нового материала!', 'danger')
                    return redirect(url_for('operation_in'))
                material = Material.query.filter_by(name=new_name, size=new_size if new_size else None).first()
                if material:
                    if material.barcode and material.barcode != new_barcode:
                        flash(f'Материал "{material.name}" уже имеет QR-код "{material.barcode}". '
                              f'Указан другой код "{new_barcode}".', 'danger')
                        return redirect(url_for('operation_in'))
                    if not material.barcode:
                        dup = Material.query.filter_by(barcode=new_barcode).first()
                        if dup:
                            flash(f'QR-код "{new_barcode}" уже используется материалом "{dup.name}"!', 'danger')
                            return redirect(url_for('operation_in'))
                        material.barcode = new_barcode
                else:
                    dup = Material.query.filter_by(barcode=new_barcode).first()
                    if dup:
                        flash(f'QR-код "{new_barcode}" уже используется материалом "{dup.name}"!', 'danger')
                        return redirect(url_for('operation_in'))
                    material = Material(
                        name=new_name, size=new_size if new_size else None,
                        barcode=new_barcode,
                        category_id=int(new_category_id) if new_category_id else None,
                        is_reusable=is_reusable,
                    )
                    db.session.add(material)
                    db.session.flush()
            else:
                flash('Выберите материал или введите название нового.', 'danger')
                return redirect(url_for('operation_in'))
            location = Location.query.filter_by(code=location_code).first() if location_code else None
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date() if expiry_str else None
            batch = Batch(
                material_id=material.id, location_id=location.id if location else None,
                batch_number=batch_number if batch_number else None,
                expiry_date=expiry_date, quantity=qty, used=0
            )
            db.session.add(batch)
            db.session.flush()
            t_type = 'reusable_in' if material.is_reusable else 'in'
            transaction = Transaction(
                user_id=current_user.id, batch_id=batch.id, type=t_type,
                quantity=qty, note=note if note else f'Приход "{material.name}"'
            )
            db.session.add(transaction)
            db.session.commit()
            flash(f'Приход: {material.name} — {qty} шт.!', 'success')
            return redirect(url_for('index'))
        materials_list = Material.query.order_by(Material.name).all()
        locations_list = Location.query.order_by(Location.code).all()
        categories_list = Category.query.order_by(Category.name).all()
        return render_template('operation_in.html',
                               materials=materials_list, locations=locations_list,
                               categories=categories_list, selected_material=None)

    # ──────────────────────────────────────────
    # РАСХОД (с корзиной)
    # ──────────────────────────────────────────
    @app.route('/operations/out', methods=['GET', 'POST'])
    @login_required
    def operation_out():
        if not current_user.can_create_draft():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        if request.method == 'POST':
            operation_id = request.form.get('operation_id', '').strip()
            doctor_name = request.form.get('doctor_name', '').strip()
            note = request.form.get('note', '').strip()
            cart_data = request.form.get('cart_data', '[]')
            cart = json.loads(cart_data)

            if current_user.role == 'xray_lab':
                oper_location = Location.query.filter_by(code=OPERATING_ROOM_LOCATION_CODE).first()
                if not oper_location:
                    flash('Локация операционной не найдена.', 'danger')
                    return redirect(url_for('operation_out'))
                for item in cart:
                    try:
                        material_id = int(item['id'])
                        quantity = int(item['qty'])
                    except (KeyError, TypeError, ValueError):
                        flash('В корзине есть некорректная позиция.', 'danger')
                        return redirect(url_for('operation_out'))
                    available = db.session.query(
                        db.func.coalesce(db.func.sum(Batch.quantity - Batch.used), 0)
                    ).filter(
                        Batch.material_id == material_id,
                        Batch.location_id == oper_location.id,
                        Batch.is_active == True,
                        Batch.quantity - Batch.used > 0
                    ).scalar() or 0
                    if quantity <= 0 or quantity > available:
                        flash('Можно оформить только доступный остаток операционной.', 'danger')
                        return redirect(url_for('operation_out'))

            if not cart or not operation_id or not doctor_name:
                flash('Заполните все обязательные поля.', 'danger')
                return redirect(url_for('operation_out'))

            SpendingDraft.query.filter_by(
                user_id=current_user.id,
                operation_id=operation_id,
                status='rejected'
            ).delete()

            if current_user.role in ('admin', 'head', 'doctor_storekeeper', 'doctor'):
                for item in cart:
                    material = Material.query.get_or_404(int(item['id']))
                    qty = int(item['qty'])
                    oper_location = Location.query.filter_by(code='03').first()
                    if not oper_location:
                        flash('Ошибка: локация операционной (03) не найдена.', 'danger')
                        return redirect(url_for('operation_out'))

                    batches = Batch.query.filter(
                        Batch.material_id == material.id,
                        Batch.location_id == oper_location.id,
                        Batch.is_active == True,
                        Batch.quantity - Batch.used > 0
                    ).order_by(Batch.expiry_date.asc()).all()
                    total_available = sum(b.remaining for b in batches)
                    if qty > total_available:
                        flash(f'Недостаточно "{material.name}"! Доступно: {total_available} шт.', 'danger')
                        return redirect(url_for('operation_out'))
                    remaining_to_take = qty
                    for batch in batches:
                        if remaining_to_take <= 0:
                            break
                        take = min(batch.remaining, remaining_to_take)
                        batch.used += take
                        remaining_to_take -= take
                        t_type = 'reusable_out' if material.is_reusable else 'out'
                        db.session.add(Transaction(
                            user_id=current_user.id, batch_id=batch.id, type=t_type,
                            quantity=take, operation_id=operation_id, doctor_name=doctor_name,
                            note=note or f'Расход "{material.name}"'
                        ))
                db.session.commit()
                flash(f'Расход по операции {operation_id}: {len(cart)} позиций!', 'success')
            else:
                for item in cart:
                    draft = SpendingDraft(
                        user_id=current_user.id, material_id=int(item['id']),
                        quantity=int(item['qty']), operation_id=operation_id,
                        doctor_name=doctor_name, note=note
                    )
                    db.session.add(draft)
                db.session.commit()
                flash(f'Черновик по операции {operation_id}: {len(cart)} позиций. Ожидает врача.', 'info')

            if current_user.role == 'xray_lab':
                return redirect(url_for('my_drafts'))
            return redirect(url_for('index'))

        prefilled_operation = request.args.get('operation_id', '').strip()
        prefilled_cart = []
        prefilled_doctor = ''
        prefilled_note = ''

        if prefilled_operation:
            rejected = SpendingDraft.query.filter_by(
                user_id=current_user.id,
                operation_id=prefilled_operation,
                status='rejected'
            ).all()
            for d in rejected:
                prefilled_cart.append({
                    'id': str(d.material_id),
                    'name': f'{d.material.name} {d.material.size or ""}'.strip(),
                    'qty': d.quantity
                })
            if rejected:
                prefilled_doctor = rejected[0].doctor_name or ''
                prefilled_note = rejected[0].note or ''

        if current_user.role == 'xray_lab':
            oper_location = Location.query.filter_by(code=OPERATING_ROOM_LOCATION_CODE).first()
            if oper_location:
                material_ids = [row[0] for row in db.session.query(Batch.material_id).filter(
                    Batch.location_id == oper_location.id,
                    Batch.is_active == True,
                    Batch.quantity - Batch.used > 0
                ).distinct().all()]
            else:
                material_ids = []
            materials_list = Material.query.filter(Material.id.in_(material_ids)).order_by(Material.name).all() if material_ids else []
            category_ids = {material.category_id for material in materials_list if material.category_id}
            categories_list = Category.query.filter(Category.id.in_(category_ids)).order_by(Category.name).all() if category_ids else []
        else:
            materials_list = Material.query.order_by(Material.name).all()
            categories_list = Category.query.order_by(Category.name).all()
        return render_template('operation_out.html',
                               materials=materials_list, categories=categories_list,
                               prefilled_operation=prefilled_operation,
                               prefilled_cart=prefilled_cart,
                               prefilled_doctor=prefilled_doctor,
                               prefilled_note=prefilled_note)

    # ──────────────────────────────────────────
    # ПОДТВЕРЖДЕНИЕ СПИСАНИЙ (по операции)
    # ──────────────────────────────────────────
    @app.route('/spending/confirm')
    @login_required
    def spending_confirm_list():
        if not current_user.can_confirm():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        drafts = SpendingDraft.query.filter_by(status='pending').order_by(
            SpendingDraft.created_at.desc()
        ).all()

        grouped = {}
        for d in drafts:
            key = d.operation_id or f'Без операции #{d.id}'
            if key not in grouped:
                grouped[key] = {
                    'operation_id': d.operation_id,
                    'created_at': d.created_at,
                    'doctor_name': d.doctor_name,
                    'creator': d.user,
                    'items': [],
                }
            grouped[key]['items'].append(d)

        return render_template('spending_confirm.html', grouped=grouped)

    @app.route('/spending/operation/<operation_id>/approve', methods=['POST'])
    @login_required
    def spending_operation_approve(operation_id):
        if not current_user.can_confirm():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        drafts = SpendingDraft.query.filter_by(
            operation_id=operation_id, status='pending'
        ).all()

        if not drafts:
            flash('Черновики не найдены.', 'warning')
            return redirect(url_for('spending_confirm_list'))

        # Списание по операции выполняется ТОЛЬКО из операционной.
        oper_location = Location.query.filter_by(code='03').first()
        if not oper_location:
            flash('Ошибка: локация операционной (03) не найдена в базе данных.', 'danger')
            return redirect(url_for('spending_confirm_list'))

        # Сначала проверяем всю операцию целиком. Если хотя бы одного
        # материала не хватает в oper, ничего не списываем.
        for draft in drafts:
            material = draft.material
            batches = Batch.query.filter(
                Batch.material_id == material.id,
                Batch.location_id == oper_location.id,
                Batch.is_active == True,
                Batch.quantity - Batch.used > 0
            ).order_by(Batch.expiry_date.asc()).all()

            total_available = sum(b.remaining for b in batches)
            if draft.quantity > total_available:
                flash(
                    f'В операционной недостаточно "{material.name}"! '
                    f'Требуется: {draft.quantity} шт., доступно: {total_available} шт.',
                    'danger'
                )
                return redirect(url_for('spending_confirm_list'))

        count = len(drafts)

        for draft in drafts:
            material = draft.material
            batches = Batch.query.filter(
                Batch.material_id == material.id,
                Batch.location_id == oper_location.id,
                Batch.is_active == True,
                Batch.quantity - Batch.used > 0
            ).order_by(Batch.expiry_date.asc()).all()

            remaining_to_take = draft.quantity
            for batch in batches:
                if remaining_to_take <= 0:
                    break

                take = min(batch.remaining, remaining_to_take)
                batch.used += take
                remaining_to_take -= take

                t_type = 'reusable_out' if material.is_reusable else 'out'
                db.session.add(Transaction(
                    user_id=current_user.id,
                    batch_id=batch.id,
                    type=t_type,
                    quantity=take,
                    operation_id=draft.operation_id,
                    doctor_name=draft.doctor_name,
                    note=f'Подтверждено врачом: {draft.note or ""}'
                ))

            db.session.delete(draft)

        db.session.commit()
        flash(
            f'Операция {operation_id}: списано {count} позиций из операционной.',
            'success'
        )
        return redirect(url_for('spending_confirm_list'))

    @app.route('/spending/operation/<operation_id>/reject', methods=['POST'])
    @login_required
    def spending_operation_reject(operation_id):
        if not current_user.can_confirm():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        drafts = SpendingDraft.query.filter_by(
            operation_id=operation_id, status='pending'
        ).all()

        if not drafts:
            flash('Черновики не найдены.', 'warning')
            return redirect(url_for('spending_confirm_list'))

        for draft in drafts:
            draft.status = 'rejected'
            draft.confirmed_by = current_user.id
            draft.confirmed_at = datetime.utcnow()

        db.session.commit()
        flash(f'Операция {operation_id} отправлена на доработку.', 'info')
        return redirect(url_for('spending_confirm_list'))

    # ──────────────────────────────────────────
    # МОИ ЧЕРНОВИКИ (для лаборанта)
    # ──────────────────────────────────────────
    @app.route('/spending/my-drafts')
    @login_required
    def my_drafts():
        if not current_user.can_view_own_drafts():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        drafts = SpendingDraft.query.filter_by(
            user_id=current_user.id
        ).order_by(SpendingDraft.created_at.desc()).all()

        grouped = {}
        for d in drafts:
            key = d.operation_id or f'Без операции #{d.id}'
            if key not in grouped:
                grouped[key] = {
                    'operation_id': d.operation_id,
                    'status': d.status,
                    'doctor_name': d.doctor_name,
                    'created_at': d.created_at,
                    'items': [],
                }
            grouped[key]['items'].append(d)
            if d.status == 'rejected':
                grouped[key]['status'] = 'rejected'

        return render_template('my_drafts.html', grouped=grouped)

    # ──────────────────────────────────────────
    # ЗАЯВКИ НА ПЕРЕМЕЩЕНИЕ
    # ──────────────────────────────────────────
    @app.route('/requests')
    @login_required
    def requests_list():
        if not (current_user.can_create_request() or current_user.can_process_requests()):
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        if current_user.can_process_requests():
            reqs = TransferRequest.query.filter_by(status='pending').order_by(TransferRequest.created_at.desc()).all()
        else:
            reqs = TransferRequest.query.filter_by(
                from_user_id=current_user.id, status='pending'
            ).order_by(TransferRequest.created_at.desc()).all()
        return render_template('requests.html', requests=reqs)

    @app.route('/requests/add', methods=['GET', 'POST'])
    @login_required
    def request_add():
        if not current_user.can_create_request():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        target_code = SPENDING_LOCATION_CODE if 'SPENDING_LOCATION_CODE' in globals() else '03'
        target = Location.query.filter_by(code=target_code).first()
        if not target:
            flash(f'Локация назначения ({target_code}) не найдена.', 'danger')
            return redirect(url_for('index'))

        if request.method == 'POST':
            mids=request.form.getlist('material_id[]')
            qs=request.form.getlist('quantity[]')
            if not mids:
                old=request.form.get('material_id','').strip(); oq=request.form.get('quantity','').strip()
                if old: mids=[old]; qs=[oq]
            note=request.form.get('note','').strip()
            if not mids or len(mids)!=len(qs):
                flash('Добавьте хотя бы одну корректную позицию.','danger')
                return redirect(url_for('request_add'))
            items=[]; seen=set()
            for n,(raw_id,raw_q) in enumerate(zip(mids,qs),1):
                try: mid=int(raw_id); q=int(raw_q)
                except (TypeError,ValueError):
                    flash(f'Позиция №{n}: некорректные данные.','danger'); return redirect(url_for('request_add'))
                if q<=0:
                    flash(f'Позиция №{n}: количество должно быть больше 0.','danger'); return redirect(url_for('request_add'))
                if mid in seen:
                    flash('Один и тот же материал нельзя добавлять дважды.','danger'); return redirect(url_for('request_add'))
                seen.add(mid); m=db.session.get(Material,mid)
                if not m:
                    flash(f'Позиция №{n}: материал не найден.','danger'); return redirect(url_for('request_add'))
                # Наличие запрошенного материала здесь не ограничивает
                # создание заявки. Если его нет, врач-кладовщик сможет
                # выбрать другой фактический материал при обработке.
                items.append((m,q))
            first_m,first_q=items[0]
            req=TransferRequest(from_user_id=current_user.id,material_id=first_m.id,quantity=first_q,note=note or None,to_location_code=target_code)
            db.session.add(req); db.session.flush()
            for m,q in items:
                db.session.add(TransferRequestItem(request_id=req.id,material_id=m.id,requested_quantity=q))
            db.session.commit()
            flash(f'Заявка №{req.id} создана. Позиций: {len(items)}.','success')
            return redirect(url_for('requests_list'))

        categories=Category.query.order_by(Category.name).all()
        mats=Material.query.order_by(Material.name).all()
        availability={}
        for m in mats:
            total=sum(
                b.remaining
                for b in m.batches
                if b.is_active and b.remaining>0 and b.location_id!=target.id
            )
            availability[m.id]=total

        # В форме заявки показываем весь каталог материалов.
        # Наличие отображается отдельно и не запрещает создать заявку,
        # даже если остаток равен 0.
        available=mats

        return render_template(
            'request_form.html',
            categories=categories,
            materials=available,
            availability=availability,
            request_target_code=target_code
        )

    @app.route('/requests/<int:id>/complete', methods=['GET', 'POST'])
    @login_required
    def request_complete(id):
        if not current_user.can_process_requests():
            flash('Недостаточно прав.','danger')
            return redirect(url_for('requests_list'))
        req=db.session.get(TransferRequest,id)
        if not req:
            flash('Заявка не найдена.','danger'); return redirect(url_for('requests_list'))
        if req.status!='pending':
            flash('Заявка уже обработана.','warning'); return redirect(url_for('requests_list'))

        if request.method=='GET':
            locations=Location.query.order_by(Location.code).all()
            target_code=req.to_location_code or '03'
            if target_code=='oper': target_code='03'
            target=Location.query.filter_by(code=target_code).first()
            sources=[x for x in locations if not target or x.id!=target.id]

            # Материалы, которые реально доступны для выдачи
            # на складах/ячейках, кроме конечной локации заявки.
            available_materials=[]
            for m in Material.query.order_by(Material.name, Material.size).all():
                available=sum(
                    b.remaining
                    for b in m.batches
                    if b.is_active
                    and b.remaining > 0
                    and (not target or b.location_id != target.id)
                )
                if available > 0:
                    available_materials.append({
                        'id': m.id,
                        'name': m.name,
                        'size': m.size or '',
                        'category_id': m.category_id,
                        'available': available
                    })

            return render_template(
                'request_process.html',
                request_obj=req,
                items=req.items,
                sources=sources,
                target_code=target_code,
                available_materials=available_materials
            )

        item_ids=request.form.getlist('request_item_id[]')
        batch_ids=request.form.getlist('batch_id[]')
        quantities=request.form.getlist('qty[]')
        issued_material_ids=request.form.getlist('issued_material_id[]')

        if not item_ids or not (len(item_ids)==len(batch_ids)==len(quantities)):
            flash('Проверьте позиции, партии и количества.','danger')
            return redirect(url_for('request_complete',id=req.id))

        items={x.id:x for x in req.items}
        item_totals={}
        batch_totals={}
        batch_items={}
        item_materials={}

        # Фактический материал передаётся один раз на каждую
        # позицию заявки, а строки партий могут быть несколькими.
        issued_by_item={}

        if issued_material_ids:
            if len(issued_material_ids) != len(items):
                flash('Для каждой позиции заявки должен быть указан фактический материал.','danger')
                return redirect(url_for('request_complete',id=req.id))

            for item_obj, raw_mid in zip(req.items, issued_material_ids):
                try:
                    issued_by_item[item_obj.id]=int(raw_mid)
                except (TypeError,ValueError):
                    flash('Некорректно указан фактический материал.','danger')
                    return redirect(url_for('request_complete',id=req.id))

        for n,(ri,bi,qr) in enumerate(zip(item_ids,batch_ids,quantities),1):
            try:
                iid=int(ri)
                bid=int(bi)
                q=int(qr)
            except (TypeError,ValueError):
                flash(f'Строка №{n}: некорректные данные.','danger')
                return redirect(url_for('request_complete',id=req.id))

            if iid not in items or q<=0:
                flash(f'Строка №{n}: проверьте позицию и количество.','danger')
                return redirect(url_for('request_complete',id=req.id))

            item=items[iid]

            # Один фактический материал задаётся для всей позиции заявки.
            # При этом одну позицию можно распределить по нескольким партиям.
            issued_mid=issued_by_item.get(iid, item.material_id)

            issued_material= db.session.get(Material, issued_mid)
            if not issued_material:
                flash(f'Строка №{n}: фактический материал не найден.','danger')
                return redirect(url_for('request_complete',id=req.id))

            # Одна позиция заявки может быть разбита по нескольким партиям,
            # но фактический материал у неё должен оставаться одним и тем же.
            prev_mid=item_materials.get(iid)
            if prev_mid is not None and prev_mid != issued_mid:
                flash(f'Строка №{n}: для одной позиции заявки указан разный фактический материал.','danger')
                return redirect(url_for('request_complete',id=req.id))
            item_materials[iid]=issued_mid

            b=db.session.get(Batch,bid)
            if not b or not b.is_active:
                flash(f'Строка №{n}: партия недоступна.','danger')
                return redirect(url_for('request_complete',id=req.id))

            target_code_check=(req.to_location_code or '03').replace('oper','03')
            target_check=Location.query.filter_by(code=target_code_check).first()
            if b.location_id == (target_check.id if target_check else -1):
                flash(f'Партия #{b.id} уже находится в назначении.','danger')
                return redirect(url_for('request_complete',id=req.id))

            # Критически важно: партия должна относиться к выбранному
            # фактическому материалу, а не обязательно к первоначально
            # запрошенному материалу.
            if b.material_id != issued_mid:
                flash(f'Строка №{n}: партия не относится к выбранному фактическому материалу.','danger')
                return redirect(url_for('request_complete',id=req.id))

            if bid in batch_items and batch_items[bid]!=iid:
                flash(f'Партия #{bid} назначена двум позициям.','danger')
                return redirect(url_for('request_complete',id=req.id))

            batch_items[bid]=iid
            batch_totals[bid]=batch_totals.get(bid,0)+q
            item_totals[iid]=item_totals.get(iid,0)+q

        batches={}
        for bid,total in batch_totals.items():
            b=db.session.get(Batch,bid)
            if b.remaining<total:
                flash(f'Партия #{b.id}: доступно {b.remaining} шт., передать пытаются {total} шт.','danger')
                return redirect(url_for('request_complete',id=req.id))
            batches[bid]=b

        target_code=req.to_location_code or '03'
        if target_code=='oper': target_code='03'
        target=Location.query.filter_by(code=target_code).first()
        if not target:
            flash('Локация назначения не найдена.','danger'); return redirect(url_for('requests_list'))

        groups={}
        for bid,total in batch_totals.items():
            b=batches[bid]; groups.setdefault(b.location_id,[]).append((b,total))
        note=request.form.get('note','').strip(); transfer_count=0
        for source_id,rows in groups.items():
            source=db.session.get(Location,source_id)
            tr=Transfer(from_location_id=source.id,to_location_id=target.id,user_id=current_user.id,request_id=req.id,note=note or f'По заявке №{req.id}')
            db.session.add(tr); db.session.flush(); transfer_count+=1
            for b,q in rows:
                iid=batch_items[b.id]
                issued_mid=item_materials[iid]

                b.used+=q

                tb=Batch.query.filter(
                    Batch.material_id==issued_mid,
                    Batch.location_id==target.id,
                    Batch.batch_number==b.batch_number,
                    Batch.expiry_date==b.expiry_date,
                    Batch.is_active==True
                ).order_by(Batch.id.asc()).first()

                if tb:
                    tb.quantity+=q
                else:
                    tb=Batch(
                        material_id=issued_mid,
                        location_id=target.id,
                        batch_number=b.batch_number,
                        expiry_date=b.expiry_date,
                        quantity=q,
                        used=0,
                        is_active=True
                    )
                    db.session.add(tb)
                    db.session.flush()

                db.session.add(
                    TransferItem(
                        transfer_id=tr.id,
                        batch_id=tb.id,
                        quantity=q,
                        request_item_id=iid
                    )
                )
                db.session.add(
                    Transaction(
                        user_id=current_user.id,
                        batch_id=b.id,
                        type='move_out',
                        quantity=q,
                        note=f'По заявке №{req.id}: в {target.code}',
                        request_item_id=iid
                    )
                )
                db.session.add(
                    Transaction(
                        user_id=current_user.id,
                        batch_id=tb.id,
                        type='move_in',
                        quantity=q,
                        note=f'По заявке №{req.id}: из {source.code}',
                        request_item_id=iid
                    )
                )

        for iid,item in items.items():
            item.transferred_quantity=item_totals.get(iid,0)
            item.transferred_material_id=item_materials.get(iid)

        req.status='completed'; req.to_user_id=current_user.id; req.processed_at=datetime.utcnow(); db.session.commit()
        flash(f'Заявка №{req.id} выполнена. Создано перемещений: {transfer_count}.','success')
        return redirect(url_for('transfers_list'))

    @app.route('/transfers')
    @login_required
    def transfers_list():
        if not current_user.can_transfer():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        transfers = Transfer.query.order_by(Transfer.created_at.desc()).limit(50).all()
        return render_template('transfers.html', transfers=transfers)

    @app.route('/transfers/add', methods=['GET', 'POST'])
    @login_required
    def transfer_add():
        if not current_user.can_transfer():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        transfer_request = None

        request_id = (
            request.args.get('request_id', type=int)
            if request.method == 'GET'
            else request.form.get('request_id', type=int)
        )

        if request_id:
            transfer_request = TransferRequest.query.filter_by(
                id=request_id,
                status='pending'
            ).first()

            if not transfer_request:
                flash('Активная заявка не найдена.', 'warning')
                return redirect(url_for('requests_list'))

        if request.method == 'POST':
            from_location_code = request.form.get(
                'from_location', ''
            ).strip()

            to_location_code = request.form.get(
                'to_location', ''
            ).strip()

            note = request.form.get('note', '').strip()

            if transfer_request:
                to_location_code = (
                    transfer_request.to_location_code or '03'
                )
                if to_location_code == 'oper':
                    to_location_code = '03'

            def error_url():
                if transfer_request:
                    return url_for(
                        'transfer_add',
                        request_id=transfer_request.id
                    )
                return url_for('transfer_add')

            if not from_location_code or not to_location_code:
                flash('Выберите обе локации.', 'danger')
                return redirect(error_url())

            if from_location_code == to_location_code:
                flash('Локации должны быть разными.', 'danger')
                return redirect(error_url())

            from_loc = Location.query.filter_by(
                code=from_location_code
            ).first()

            to_loc = Location.query.filter_by(
                code=to_location_code
            ).first()

            if not from_loc or not to_loc:
                flash('Локация не найдена.', 'danger')
                return redirect(error_url())

            batch_ids = request.form.getlist('batch_id[]')
            quantities = request.form.getlist('qty[]')

            if not batch_ids:
                indexed = {}

                for key in request.form.keys():
                    if key.startswith('batch_id_'):
                        suffix = key[len('batch_id_'):]
                        if suffix.isdigit():
                            i = int(suffix)
                            indexed.setdefault(i, {})['batch_id'] = request.form.get(key)

                    elif key.startswith('qty_'):
                        suffix = key[len('qty_'):]
                        if suffix.isdigit():
                            i = int(suffix)
                            indexed.setdefault(i, {})['qty'] = request.form.get(key)

                for i in sorted(indexed):
                    row = indexed[i]
                    if 'batch_id' in row and 'qty' in row:
                        batch_ids.append(row['batch_id'])
                        quantities.append(row['qty'])

            if not batch_ids:
                flash('Добавьте хотя бы одну партию для перемещения.', 'danger')
                return redirect(error_url())

            if len(batch_ids) != len(quantities):
                flash(
                    'Ошибка данных формы перемещения. Повторите операцию.',
                    'danger'
                )
                return redirect(error_url())

            transfer_rows = []
            seen = set()

            for raw_batch_id, raw_qty in zip(batch_ids, quantities):
                try:
                    batch_id = int((raw_batch_id or '').strip())
                    qty = int((raw_qty or '').strip())
                except (TypeError, ValueError):
                    flash(
                        'Некорректные данные партии или количества.',
                        'danger'
                    )
                    return redirect(error_url())

                if qty <= 0:
                    flash('Количество должно быть больше нуля.', 'danger')
                    return redirect(error_url())

                if batch_id in seen:
                    flash(
                        'Одна и та же партия выбрана несколько раз.',
                        'danger'
                    )
                    return redirect(error_url())
                seen.add(batch_id)

                batch = db.session.get(Batch, batch_id)

                if not batch:
                    flash(f'Партия #{batch_id} не найдена.', 'danger')
                    return redirect(error_url())

                if batch.location_id != from_loc.id:
                    flash(
                        f'Партия #{batch.id} не находится '
                        f'в локации "{from_loc.code}".',
                        'danger'
                    )
                    return redirect(error_url())

                if not batch.is_active:
                    flash(f'Партия #{batch.id} неактивна.', 'danger')
                    return redirect(error_url())

                if batch.remaining < qty:
                    flash(
                        f'Недостаточно материала в партии #{batch.id}. '
                        f'Доступно: {batch.remaining}, '
                        f'запрошено: {qty}.',
                        'danger'
                    )
                    return redirect(error_url())

                transfer_rows.append((batch, qty))

            if transfer_request:
                fulfilled = sum(
                    qty for batch, qty in transfer_rows
                    if batch.material_id == transfer_request.material_id
                )

                if fulfilled != transfer_request.quantity:
                    flash(
                        f'Заявка №{transfer_request.id} требует '
                        f'{transfer_request.quantity} шт. материала '
                        f'«{transfer_request.material.name}». '
                        f'В текущем перемещении указано: {fulfilled} шт.',
                        'danger'
                    )
                    return redirect(
                        url_for(
                            'transfer_add',
                            request_id=transfer_request.id
                        )
                    )

            transfer = Transfer(
                from_location_id=from_loc.id,
                to_location_id=to_loc.id,
                user_id=current_user.id,
                note=note
            )
            db.session.add(transfer)
            db.session.flush()

            for batch, qty in transfer_rows:
                batch.used += qty

                target_batch = Batch.query.filter(
                    Batch.material_id == batch.material_id,
                    Batch.location_id == to_loc.id,
                    Batch.batch_number == batch.batch_number,
                    Batch.expiry_date == batch.expiry_date,
                    Batch.is_active == True
                ).order_by(Batch.id.asc()).first()

                if target_batch:
                    target_batch.quantity += qty
                    target_batch_id = target_batch.id
                else:
                    target_batch = Batch(
                        material_id=batch.material_id,
                        location_id=to_loc.id,
                        batch_number=batch.batch_number,
                        expiry_date=batch.expiry_date,
                        quantity=qty,
                        used=0,
                        is_active=True
                    )
                    db.session.add(target_batch)
                    db.session.flush()
                    target_batch_id = target_batch.id

                db.session.add(
                    TransferItem(
                        transfer_id=transfer.id,
                        batch_id=target_batch_id,
                        quantity=qty
                    )
                )

                db.session.add(
                    Transaction(
                        user_id=current_user.id,
                        batch_id=batch.id,
                        type='move_out',
                        quantity=qty,
                        note=f'Перемещение в {to_loc.code}'
                    )
                )

                db.session.add(
                    Transaction(
                        user_id=current_user.id,
                        batch_id=target_batch_id,
                        type='move_in',
                        quantity=qty,
                        note=f'Перемещение из {from_loc.code}'
                    )
                )

            if transfer_request:
                transfer_request.status = 'completed'
                transfer_request.to_user_id = current_user.id
                transfer_request.processed_at = datetime.utcnow()

            db.session.commit()

            if transfer_request:
                flash(
                    f'Заявка №{transfer_request.id} выполнена. '
                    f'Перемещение подтверждено!',
                    'success'
                )
            else:
                flash(
                    f'Перемещение выполнено! '
                    f'Позиций: {len(transfer_rows)}.',
                    'success'
                )

            return redirect(url_for('transfers_list'))

        locations_list = Location.query.order_by(
            Location.code
        ).all()

        return render_template(
            'transfer_form.html',
            locations=locations_list,
            transfer_request=transfer_request
        )



    @app.route('/api/batches-by-location')
    @login_required
    def api_batches_by_location():
        location_code = request.args.get('location', '').strip()
        if current_user.role == 'xray_lab' and location_code != OPERATING_ROOM_LOCATION_CODE:
            return []
        if not location_code:
            return []
        location = Location.query.filter_by(code=location_code).first()
        if not location:
            return []
        batches = Batch.query.filter(
            Batch.location_id == location.id, Batch.is_active == True,
            Batch.quantity - Batch.used > 0
        ).order_by(Batch.expiry_date.asc()).all()
        return [{
            'id': b.id,
            'material_id': b.material_id,
            'name': b.material.name,
            'size': b.material.size or '',
            'remaining': b.remaining,
            'expiry': b.expiry_date.strftime('%d.%m.%Y') if b.expiry_date else '—',
        } for b in batches]

    # ──────────────────────────────────────────
    # РЕВИЗИЯ
    # ──────────────────────────────────────────
    @app.route('/revision', methods=['GET', 'POST'])
    @login_required
    def revision():
        if not current_user.can_revision():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        if request.method == 'POST':
            batch_id = request.form.get('batch_id')
            new_quantity = int(request.form.get('new_quantity', 0))
            new_used = int(request.form.get('new_used', 0))
            note = request.form.get('note', '').strip()
            batch = Batch.query.get_or_404(int(batch_id))
            old_quantity = batch.quantity
            old_used = batch.used
            batch.quantity = new_quantity
            batch.used = new_used
            revision_record = Revision(
                user_id=current_user.id, batch_id=batch.id,
                old_quantity=old_quantity, new_quantity=new_quantity,
                old_used=old_used, new_used=new_used,
                note=f'Ревизия от {date.today().strftime("%d.%m.%Y")}. {note}'
            )
            db.session.add(revision_record)
            db.session.add(Transaction(
                user_id=current_user.id, batch_id=batch.id, type='revision',
                quantity=abs(new_quantity - old_quantity),
                note=f'Ревизия: qty {old_quantity}→{new_quantity}, used {old_used}→{new_used}'
            ))
            db.session.commit()
            flash('Ревизия проведена!', 'success')
            return redirect(url_for('revision'))
        location_code = request.args.get('location', '').strip()
        location = Location.query.filter_by(code=location_code).first() if location_code else None
        batches = []
        if location:
            batches = Batch.query.filter(
                Batch.location_id == location.id, Batch.is_active == True
            ).order_by(Batch.expiry_date.asc()).all()
        locations_list = Location.query.order_by(Location.code).all()
        return render_template('revision.html', locations=locations_list, batches=batches, selected_location=location_code)

    # ──────────────────────────────────────────
    # ЖУРНАЛ ОПЕРАЦИЙ
    # ──────────────────────────────────────────
    @app.route('/transactions')
    @login_required
    def transactions():
        if not current_user.can_view_journal():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))
        transactions_list = Transaction.query.order_by(Transaction.created_at.desc()).limit(100).all()
        return render_template('transactions.html', transactions=transactions_list)

    # ──────────────────────────────────────────
    # ЭКСПОРТ В EXCEL
    # ──────────────────────────────────────────
    @app.route('/export/excel')
    @login_required
    def export_excel():
        if not current_user.can_reports():
            flash('Недостаточно прав для экспорта.', 'danger')
            return redirect(url_for('index'))

        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = Workbook()
        ws_default = wb.active
        ws_default.title = "Пусто"
        first_sheet = True

        today = date.today()
        locations = Location.query.order_by(Location.code).all()

        header_fill = PatternFill(start_color='0d6efd', end_color='0d6efd', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True, size=11)
        cat_fill = PatternFill(start_color='cfe2ff', end_color='cfe2ff', fill_type='solid')
        cat_font = Font(bold=True, size=11)
        red_fill = PatternFill(start_color='ffe0e0', end_color='ffe0e0', fill_type='solid')
        yellow_fill = PatternFill(start_color='fff3cd', end_color='fff3cd', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin')
        )
        headers = ['Наименование', 'Размер', 'Штрихкод', 'Партия',
                   'Срок годности', 'Приход', 'Расход', 'Остаток', 'Статус']

        for loc in locations:
            batches = Batch.query.filter(
                Batch.location_id == loc.id,
                Batch.is_active == True
            ).order_by(Batch.expiry_date.asc()).all()

            if not batches:
                continue

            sheet_name = f'{loc.code} - {loc.description}'[:31]
            if first_sheet:
                ws = ws_default
                ws.title = sheet_name
                first_sheet = False
            else:
                ws = wb.create_sheet(title=sheet_name)

            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=header)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = thin_border

            categories_dict = {}
            for b in batches:
                cat_name = b.material.category_name or 'Без категории'
                if cat_name not in categories_dict:
                    categories_dict[cat_name] = []
                categories_dict[cat_name].append(b)

            row = 2
            for cat_name in sorted(categories_dict.keys()):
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(headers))
                cell = ws.cell(row=row, column=1, value=f'{cat_name}')
                cell.fill = cat_fill
                cell.font = cat_font
                cell.border = thin_border
                row += 1

                for b in categories_dict[cat_name]:
                    remaining = b.remaining
                    if remaining == 0 and b.quantity == 0:
                        continue
                    status = 'Активен'
                    fill = None
                    if b.expiry_date and b.expiry_date < today:
                        status = 'Просрочен'
                        fill = red_fill
                    elif b.expiry_date and (b.expiry_date - today).days <= 90:
                        status = 'Истекает'
                        fill = yellow_fill

                    data = [
                        b.material.name,
                        b.material.size or '',
                        b.material.barcode or '',
                        b.batch_number or '',
                        b.expiry_date.strftime('%d.%m.%Y') if b.expiry_date else '',
                        b.quantity,
                        b.used,
                        remaining,
                        status,
                    ]
                    for col, value in enumerate(data, 1):
                        cell = ws.cell(row=row, column=col, value=value)
                        cell.border = thin_border
                        if fill:
                            cell.fill = fill
                    row += 1

            for col in ws.columns:
                max_length = 0
                col_letter = col[0].column_letter
                for cell in col:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = min(max_length + 2, 40)

        if len(wb.sheetnames) > 1 and "Пусто" in wb.sheetnames:
            wb.remove(wb["Пусто"])

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f'Остатки_по_складам_{today.strftime("%d.%m.%Y")}.xlsx'
        return send_file(output, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True)

    # ──────────────────────────────────────────
    # УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (админ)
    # ──────────────────────────────────────────
    @app.route('/admin/users')
    @login_required
    def admin_users():
        if not current_user.is_admin():
            flash('Только для администратора.', 'danger')
            return redirect(url_for('index'))
        users = User.query.order_by(User.role, User.username).all()
        return render_template('admin_users.html', users=users)

    @app.route('/admin/users/add', methods=['GET', 'POST'])
    @login_required
    def admin_user_add():
        if not current_user.is_admin():
            flash('Только для администратора.', 'danger')
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
            full_name = request.form.get('full_name', '').strip()
            role = request.form.get('role', '').strip()
            if not username or not password or not role:
                flash('Логин, пароль и роль обязательны!', 'danger')
                return render_template('admin_user_form.html', user=None)
            if User.query.filter_by(username=username).first():
                flash(f'Логин "{username}" уже занят!', 'danger')
                return render_template('admin_user_form.html', user=None)
            user = User(username=username, full_name=full_name, role=role,
                        can_revision_extra=False)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'Пользователь "{username}" создан!', 'success')
            return redirect(url_for('admin_users'))
        return render_template('admin_user_form.html', user=None)

    @app.route('/admin/users/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    def admin_user_edit(id):
        if not current_user.is_admin():
            flash('Только для администратора.', 'danger')
            return redirect(url_for('index'))
        user = User.query.get_or_404(id)
        if request.method == 'POST':
            new_username = request.form.get('username', '').strip()
            existing = User.query.filter(User.username == new_username, User.id != id).first()
            if existing:
                flash(f'Логин "{new_username}" уже занят!', 'danger')
                return render_template('admin_user_form.html', user=user)
            user.username = new_username
            user.full_name = request.form.get('full_name', '').strip()
            user.role = request.form.get('role', '').strip()
            password = request.form.get('password', '').strip()
            if password:
                user.set_password(password)
            user.is_active = request.form.get('is_active') == '1'
            user.can_revision_extra = False
            db.session.commit()
            flash(f'Пользователь "{user.username}" обновлён!', 'success')
            return redirect(url_for('admin_users'))
        return render_template('admin_user_form.html', user=user)

    @app.route('/admin/users/<int:id>/delete', methods=['POST'])
    @login_required
    def admin_user_delete(id):
        if not current_user.is_admin():
            flash('Только для администратора.', 'danger')
            return redirect(url_for('index'))
        if id == current_user.id:
            flash('Нельзя удалить самого себя!', 'danger')
            return redirect(url_for('admin_users'))
        user = User.query.get_or_404(id)
        username = user.username
        db.session.delete(user)
        db.session.commit()
        flash(f'Пользователь "{username}" удалён.', 'info')
        return redirect(url_for('admin_users'))

    # ──────────────────────────────────────────
    # API: список врачей
    # ──────────────────────────────────────────
    @app.route('/api/doctors')
    @login_required
    def api_doctors():
        doctors = User.query.filter(
            User.role.in_(['doctor', 'doctor_storekeeper', 'head']),
            User.is_active == True
        ).order_by(User.full_name).all()
        return [{'id': u.id, 'name': u.full_name or u.username} for u in doctors]

    # ──────────────────────────────────────────
    # АНАЛИТИКА (заведующий, админ)
    # ──────────────────────────────────────────
    @app.route('/analytics')
    @login_required
    def analytics():
        if not current_user.can_reports():
            flash('Недостаточно прав.', 'danger')
            return redirect(url_for('index'))

        from collections import Counter

        start_str = request.args.get('start', '')
        end_str = request.args.get('end', '')

        query = Transaction.query.filter(
            Transaction.type.in_(['out', 'reusable_out'])
        )

        if start_str:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
            query = query.filter(Transaction.created_at >= start_date)
        if end_str:
            end_date = datetime.strptime(end_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            query = query.filter(Transaction.created_at <= end_date)

        transactions = query.order_by(Transaction.created_at.desc()).all()

        total_operations = len(set(t.operation_id for t in transactions if t.operation_id))
        total_items = sum(t.quantity for t in transactions)

        doctor_stats = Counter()
        for t in transactions:
            if t.doctor_name:
                doctor_stats[t.doctor_name] += t.quantity
        top_doctors = doctor_stats.most_common(10)

        material_stats = Counter()
        for t in transactions:
            name = t.batch.material.name if t.batch and t.batch.material else '—'
            material_stats[name] += t.quantity
        top_materials = material_stats.most_common(10)

        return render_template('analytics.html',
                               transactions=transactions,
                               total_operations=total_operations,
                               total_items=total_items,
                               top_doctors=top_doctors,
                               top_materials=top_materials,
                               start=start_str, end=end_str)

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5050))
    app.run(debug=False, host='0.0.0.0', port=port)
